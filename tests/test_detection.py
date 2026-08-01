#!/usr/bin/env python3
"""Functional tests for achievement detection hooks.

These tests load the plugin module with a temporary HERMES_HOME and drive
the hook functions (_post_tool_call, _post_llm_call, _on_session_start,
_on_session_end) with synthetic kwargs matching what the Hermes gateway
passes, verifying achievements actually unlock.

Run with:  python3 -m pytest tests/  -xvs
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_FILE = os.path.join(PLUGIN_DIR, "__init__.py")
LOCALES_DIR = os.path.join(PLUGIN_DIR, "locales")


def _make_module(tmp_home: str):
    """Import the plugin in a controlled way with a temp HERMES_HOME."""
    os.environ["HERMES_HOME"] = tmp_home
    os.makedirs(os.path.join(tmp_home, "plugins", "achievements"), exist_ok=True)
    shutil.copytree(LOCALES_DIR, os.path.join(tmp_home, "plugins", "achievements", "locales"))
    spec = importlib.util.spec_from_file_location("achievements_plugin_test", PLUGIN_FILE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["achievements_plugin_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class HookTestBase(unittest.TestCase):
    """Shared fixture: temp HERMES_HOME + fresh module per test."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ach-test-")
        self.mod = _make_module(self._tmp)
        self.fresh()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)

    def fresh(self):
        self.mod._state = None
        self.mod._locales_cache = {}

    def unlocked(self, ach_id):
        return self.mod._load_state()["achievements"].get(ach_id, {}).get("unlocked", False)

    def stats(self):
        return self.mod._load_state()["stats"]

    def tool_call(self, tool_name, args=None, session_id="sess", duration_ms=100):
        self.mod._post_tool_call(
            tool_name=tool_name, args=args or {}, session_id=session_id,
            duration_ms=duration_ms,
        )

    def turn(self, message="hello", history=None, model="m1", platform="cli"):
        hist = history or [{"role": "user", "content": message}]
        self.mod._post_llm_call(
            user_message=message, conversation_history=hist,
            model=model, platform=platform,
        )


class TestTurnCounting(HookTestBase):
    """Message thresholds must accumulate across sessions."""

    def test_total_turns_accumulates(self):
        for _ in range(10):
            self.turn()
        self.assertEqual(self.stats()["total_turns"], 10)
        self.assertFalse(self.unlocked("chatty"))

    def test_chatty_at_25(self):
        for _ in range(25):
            self.turn()
        self.assertTrue(self.unlocked("chatty"))

    def test_turn_id_string_ignored(self):
        # Hermes passes turn_id as "session:task:hex" — must not crash or
        # use it as the cumulative counter
        self.mod._post_llm_call(
            user_message="x",
            conversation_history=[{"role": "user", "content": "x"}],
            model="m1", platform="cli", turn_id="sess:task:abc123",
        )
        self.assertEqual(self.stats()["total_turns"], 1)


class TestSessionScopedThresholds(HookTestBase):
    """Session-scoped achievements use per-session counts, not cumulative."""

    def test_power_session_unlocks_at_50_in_session(self):
        for i in range(60):
            self.tool_call("terminal", {"command": f"echo {i}"}, session_id="sess-A")
        self.assertTrue(self.unlocked("power_session"))
        self.assertFalse(self.unlocked("the_90_turn_club"))
        self.assertEqual(self.stats()["active_session"]["calls"], 60)

    def test_session_switch_resets_counters(self):
        for _ in range(30):
            self.tool_call("terminal", {"command": "x"}, session_id="sess-B")
        self.tool_call("read_file", {"path": "/tmp/a"}, session_id="sess-C")
        self.assertEqual(self.stats()["active_session"]["id"], "sess-C")
        self.assertEqual(self.stats()["active_session"]["calls"], 1)
        # Cumulative totals persist across sessions
        self.assertEqual(self.stats()["tools_used"]["terminal"], 30)

    def test_terminal_jockey_cumulative(self):
        for i in range(25):
            self.tool_call("terminal", {"command": f"echo {i}"}, session_id=f"s{i // 10}")
        self.assertTrue(self.unlocked("terminal_jockey"))

    def test_tool_collector_distinct_names_not_categories(self):
        # read_file/write_file/search_files are 3 distinct tools but one category
        for t in ["terminal", "read_file", "write_file", "search_files", "browser_navigate"]:
            self.tool_call(t)
        self.assertTrue(self.unlocked("tool_collector"))

    def test_complete_toolset_survives_unknown_tools(self):
        # Using a tool outside _TOOL_CATEGORIES (e.g. `process`) must not
        # brick the exact-equality check — superset comparison is required
        self.tool_call("process", {}, session_id="sess-unk")  # unknown tool
        for t in self.mod._ALL_TOOL_TYPES:
            self.tool_call(t, {}, session_id="sess-unk")
        self.assertTrue(self.unlocked("complete_toolset"))
        self.assertTrue(self.unlocked("tool_diversity"))

    def test_code_wizard_needs_10_executions(self):
        self.tool_call("execute_code", {"code": "print(1)"})
        self.assertFalse(self.unlocked("code_wizard"))
        for _ in range(9):
            self.tool_call("execute_code", {"code": "print(1)"})
        self.assertTrue(self.unlocked("code_wizard"))

    def test_session_detective_needs_10_searches(self):
        self.tool_call("session_search", {"query": "x"})
        self.assertFalse(self.unlocked("session_detective"))
        for _ in range(9):
            self.tool_call("session_search", {"query": "x"})
        self.assertTrue(self.unlocked("session_detective"))

    def test_shared_threshold_progress_uses_max_not_flicker(self):
        # web_search 13 + web_extract 12: neither crosses 25 alone, but the
        # deep_diver progress must show the best (13), not bounce 13→12
        for _ in range(13):
            self.tool_call("web_search", {"query": "x"})
        for _ in range(12):
            self.tool_call("web_extract", {"url": "x"})
        prog = self.mod._load_state()["achievements"]["deep_diver"].get("progress", {})
        self.assertEqual(prog.get("current"), 13)
        self.assertFalse(self.unlocked("deep_diver"))
        # One more web_search → crosses 25 → unlocks
        for _ in range(12):
            self.tool_call("web_search", {"query": "x"})
        self.assertTrue(self.unlocked("deep_diver"))

    def test_workflow_builder_needs_8_types(self):
        tools = ["terminal", "read_file", "write_file", "search_files",
                 "browser_navigate", "execute_code", "memory", "cronjob"]
        for t in tools:
            self.tool_call(t)
        self.assertTrue(self.unlocked("workflow_builder"))
        self.assertFalse(self.unlocked("tool_hoarder"))  # 10 cumulative needed


class TestArgumentBased(HookTestBase):
    """Achievements detected from tool arguments."""

    def test_cron_commander_only_on_create(self):
        self.tool_call("cronjob", {"action": "list"})
        self.assertFalse(self.unlocked("cron_commander"))
        self.tool_call("cronjob", {"action": "create", "schedule": "every 2h"})
        self.assertTrue(self.unlocked("cron_commander"))

    def test_precision_scheduler_iso(self):
        self.tool_call("cronjob", {"action": "create", "schedule": "2026-08-01T09:00:00", "repeat": "once"})
        self.assertTrue(self.unlocked("precision_scheduler"))

    def test_precision_scheduler_repeat_once(self):
        self.tool_call("cronjob", {"action": "create", "schedule": "30m", "repeat": "once"})
        self.assertTrue(self.unlocked("precision_scheduler"))

    def test_chain_reaction(self):
        self.tool_call("cronjob", {"action": "create", "schedule": "every 2h", "context_from": ["job-1"]})
        self.assertTrue(self.unlocked("chain_reaction"))

    def test_env_tuner_workdir(self):
        self.tool_call("cronjob", {"action": "create", "schedule": "30m", "workdir": "/home/x/proj"})
        self.assertTrue(self.unlocked("env_tuner"))

    def test_parallel_master_three_tasks(self):
        self.tool_call("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}, {"goal": "c"}]})
        self.assertTrue(self.unlocked("parallel_master"))
        self.assertEqual(self.stats()["parallel_spawns"], 3)

    def test_parallel_master_requires_three(self):
        self.tool_call("delegate_task", {"tasks": [{"goal": "a"}, {"goal": "b"}]})
        self.assertFalse(self.unlocked("parallel_master"))

    def test_skill_author_only_create(self):
        self.tool_call("skill_manage", {"action": "patch", "name": "s1"})
        self.assertFalse(self.unlocked("skill_author"))
        self.tool_call("skill_manage", {"action": "create", "name": "s1"})
        self.assertTrue(self.unlocked("skill_author"))
        self.assertEqual(self.stats()["skills_created"], 1)

    def test_memory_holder_only_saves(self):
        self.tool_call("memory", {"action": "remove", "target": "user", "old_text": "x"})
        self.assertFalse(self.unlocked("memory_holder"))
        self.tool_call("memory", {"action": "add", "target": "user", "content": "fact"})
        self.assertTrue(self.unlocked("memory_holder"))

    def test_skill_artisan_five_creates(self):
        for i in range(5):
            self.tool_call("skill_manage", {"action": "create", "name": f"s{i}"})
        self.assertTrue(self.unlocked("skill_artisan"))
        self.assertFalse(self.unlocked("skill_virtuoso"))

    def test_completionist_requires_all_others(self):
        # Unlock every non-completionist achievement except one
        others = [aid for aid in self.mod.NON_COMPLETIONIST_IDS]
        last = others.pop()
        for aid in others:
            self.mod._unlock(aid)
        self.mod._check_completionist()
        self.assertFalse(self.unlocked("completionist"))
        # Unlock the final one → completionist fires
        self.mod._unlock(last)
        self.mod._check_completionist()
        self.assertTrue(self.unlocked("completionist"))

    def test_group_completion_unlocks(self):
        # Unlock all Getting Started achievements → complete_getting_started
        gs_ids = [aid for aid, adef in self.mod.ACHIEVEMENT_DEFS.items()
                  if adef["group"] == "Getting Started"]
        for aid in gs_ids:
            self.mod._unlock(aid)
        self.mod._check_group_completions()
        self.assertTrue(self.unlocked("complete_getting_started"))

    def test_rarity_collection_unlocks(self):
        # Unlock all Rare achievements → complete_rare
        rare_ids = [aid for aid, adef in self.mod.ACHIEVEMENT_DEFS.items()
                    if adef["rarity"] == "rare"]
        for aid in rare_ids:
            self.mod._unlock(aid)
        self.mod._check_group_completions()
        self.assertTrue(self.unlocked("complete_rare"))

    def test_plugin_developer(self):
        self.tool_call("write_file", {"path": "/home/x/plugins/myplugin/plugin.yaml", "content": "name: myplugin"})
        self.assertTrue(self.unlocked("plugin_developer"))

    def test_hook_master_three_hook_types(self):
        self.tool_call("write_file", {
            "path": "/home/x/plugins/myplugin/__init__.py",
            "content": (
                'ctx.register_hook("post_llm_call", f)\n'
                'ctx.register_hook("post_tool_call", g)\n'
                'ctx.register_hook("on_session_end", h)\n'
            ),
        })
        self.assertTrue(self.unlocked("hook_master"))
        self.assertEqual(self.stats()["hooks_used"],
                         {"post_llm_call", "post_tool_call", "on_session_end"})


class TestCounterAchievements(HookTestBase):
    """Tiered achievements from recurring user commands."""

    def test_config_guru_at_15_changes(self):
        for _ in range(16):
            self.turn("run hermes config set theme dark")
        self.assertTrue(self.unlocked("config_guru"))
        self.assertEqual(self.stats()["config_changes"], 16)

    def test_plugin_pack_at_5(self):
        for _ in range(5):
            self.turn("hermes plugins enable foo")
        self.assertTrue(self.unlocked("plugin_pack"))
        self.assertTrue(self.unlocked("plugin_power"))

    def test_skill_finder_install(self):
        self.turn("hermes skills install web-search")
        self.assertTrue(self.unlocked("skill_finder"))

    def test_yolo_champion_at_25(self):
        for _ in range(25):
            self.turn("hermes run --yolo task")
        self.assertTrue(self.unlocked("yolo_champion"))


class TestQuickDraw(HookTestBase):
    """5 consecutive fast tool calls unlock Quick Draw."""

    def test_fast_streak_unlocks(self):
        for _ in range(5):
            self.tool_call("terminal", {"command": "echo fast"}, duration_ms=100)
        self.assertTrue(self.unlocked("quick_draw"))

    def test_slow_call_breaks_streak(self):
        for _ in range(4):
            self.tool_call("terminal", {"command": "echo fast"}, duration_ms=100)
        self.tool_call("terminal", {"command": "slow"}, duration_ms=60000)
        self.assertFalse(self.unlocked("quick_draw"))


class TestSessionCounting(HookTestBase):
    """on_session_start drives the Persistent achievement."""

    def test_persistent_at_three_sessions(self):
        for sid in ("s1", "s2", "s3"):
            self.mod._on_session_start(session_id=sid)
        self.turn()
        self.assertTrue(self.unlocked("persistent"))
        self.assertEqual(self.stats()["total_sessions"], 3)


class TestSubagentStop(HookTestBase):
    """subagent_stop: per-child counting for delegation achievements."""

    def test_army_commander_counts_children_not_calls(self):
        # A single delegate_task with 3 tasks spawns 3 children — each
        # child fires subagent_stop, so 9 calls with 3 tasks = 27 children
        for _ in range(9):
            for _ in range(3):
                self.mod._on_subagent_stop(
                    parent_session_id="s", child_role="leaf",
                    child_status="completed", duration_ms=1000,
                )
        self.assertTrue(self.unlocked("army_commander"))
        self.assertEqual(self.stats()["subagents_spawned"], 27)

    def test_army_commander_progress_before_threshold(self):
        for _ in range(5):
            self.mod._on_subagent_stop(
                parent_session_id="s", child_role="leaf",
                child_status="completed", duration_ms=1000,
            )
        self.assertFalse(self.unlocked("army_commander"))
        st = self.mod._load_state()["achievements"]["army_commander"]
        self.assertEqual(st["progress"]["current"], 5)
        self.assertEqual(st["progress"]["target"], 25)

    def test_orchestrator_role_unlocks(self):
        self.mod._on_subagent_stop(
            parent_session_id="s", child_role="orchestrator",
            child_status="completed", duration_ms=1000,
        )
        self.assertTrue(self.unlocked("orchestrator"))

    def test_leaf_role_does_not_unlock_orchestrator(self):
        self.mod._on_subagent_stop(
            parent_session_id="s", child_role="leaf",
            child_status="completed", duration_ms=1000,
        )
        self.assertFalse(self.unlocked("orchestrator"))

    def test_failed_child_unlocks_resilient(self):
        self.mod._on_subagent_stop(
            parent_session_id="s", child_role="leaf",
            child_status="failed", duration_ms=1000,
        )
        self.assertTrue(self.unlocked("resilient"))
        self.assertEqual(self.stats()["subagents_failed"], 1)

    def test_completed_children_do_not_unlock_resilient(self):
        for _ in range(5):
            self.mod._on_subagent_stop(
                parent_session_id="s", child_role="leaf",
                child_status="completed", duration_ms=1000,
            )
        self.assertFalse(self.unlocked("resilient"))
        self.assertEqual(self.stats()["subagents_failed"], 0)


class TestApprovalResponse(HookTestBase):
    """post_approval_response: approval choices drive trust/yolo/caution."""

    def test_always_unlocks_trust_fall_and_yolo_mode(self):
        self.mod._on_approval_response(
            command="rm -rf /tmp/x", description="dangerous",
            pattern_key="rm_rf", session_key="s", surface="gateway",
            choice="always",
        )
        self.assertTrue(self.unlocked("trust_fall"))
        self.assertTrue(self.unlocked("yolo_mode"))
        self.assertEqual(self.stats()["approvals_always"], 1)
        self.assertEqual(self.stats()["yolo_tasks"], 1)

    def test_deny_unlocks_cautious(self):
        self.mod._on_approval_response(
            command="rm -rf /tmp/x", description="dangerous",
            pattern_key="rm_rf", session_key="s", surface="gateway",
            choice="deny",
        )
        self.assertTrue(self.unlocked("cautious"))
        self.assertEqual(self.stats()["approvals_denied"], 1)
        self.assertFalse(self.unlocked("trust_fall"))

    def test_once_and_session_do_not_unlock_anything(self):
        for choice in ("once", "session", "timeout"):
            self.mod._on_approval_response(
                command="cmd", description="d", pattern_key="k",
                session_key="s", surface="cli", choice=choice,
            )
        self.assertFalse(self.unlocked("trust_fall"))
        self.assertFalse(self.unlocked("cautious"))
        self.assertFalse(self.unlocked("yolo_mode"))

    def test_yolo_champion_counts_always_choices(self):
        for _ in range(25):
            self.mod._on_approval_response(
                command="cmd", description="d", pattern_key="k",
                session_key="s", surface="cli", choice="always",
            )
        self.assertTrue(self.unlocked("yolo_champion"))


class TestSessionReset(HookTestBase):
    """on_session_reset: /new rotations drive Fresh Start."""

    def test_first_reset_unlocks_fresh_start(self):
        self.mod._on_session_reset(session_id="new-1", platform="discord")
        self.assertTrue(self.unlocked("fresh_start"))
        self.assertEqual(self.stats()["session_resets"], 1)

    def test_reset_counts_accumulate(self):
        for i in range(3):
            self.mod._on_session_reset(session_id=f"new-{i}", platform="discord")
        self.assertEqual(self.stats()["session_resets"], 3)


class TestPerTurnSignals(HookTestBase):
    """Message-derived achievements."""

    def test_first_steps_and_multi_lingual(self):
        self.turn("hola mundo ¿cómo estás?", platform="discord")
        self.assertTrue(self.unlocked("first_steps"))
        self.assertTrue(self.unlocked("multi_lingual"))
        self.assertTrue(self.unlocked("gateway_guru"))

    def test_slash_commander(self):
        self.turn("/title my session")
        self.turn("/help")
        self.turn("/new")
        self.assertTrue(self.unlocked("slash_commander"))


class TestStatePersistence(HookTestBase):
    """State round-trips through JSON without losing set fields."""

    def test_round_trip(self):
        self.tool_call("terminal", {}, session_id="sess-H")
        self.turn("hi")
        self.mod._save_state(force=True)
        self.mod._state = None  # simulate process reload
        st = self.mod._load_state()["stats"]
        self.assertEqual(st["tools_used"]["terminal"], 1)
        self.assertIsInstance(st["platforms"], set)
        self.assertIsInstance(st["active_session"]["tool_names"], set)

    def test_newly_unlocked_capped(self):
        # Unlock more than 20 achievements; list must stay bounded
        ids = list(self.mod.ACHIEVEMENT_DEFS.keys())[:30]
        for aid in ids:
            self.mod._unlock(aid)
        self.assertLessEqual(len(self.mod._load_state()["newly_unlocked"]), 20)

    def test_discord_notification_is_threaded(self):
        # _send_discord_notification must return immediately (daemon thread)
        import threading
        ach_def = self.mod.ACHIEVEMENT_DEFS["first_steps"]
        threads_before = threading.active_count()
        self.mod._send_discord_notification(ach_def)
        self.assertLessEqual(threading.active_count(), threads_before + 1)

    def test_model_platform_progress_survives_restart(self):
        # Model/platform diversity is read from persisted stats, so a
        # gateway restart must not regress progress toward the tiers
        self.turn("hello", model="model-a", platform="discord")
        self.turn("hello2", model="model-b", platform="discord")
        self.mod._save_state(force=True)
        self.mod._state = None  # simulate gateway restart
        self.turn("hello3", model="model-a", platform="cli")
        self.assertTrue(self.unlocked("model_hopper"))
        self.assertTrue(self.unlocked("cross_platform"))
        self.assertTrue(self.unlocked("gateway_guru"))

    def test_backup_created_on_save(self):
        self.tool_call("terminal", {}, session_id="sess-bak")
        self.mod._save_state(force=True)
        self.assertTrue(os.path.exists(self.mod._STATE_BAK_PATH))

    def test_corrupted_state_recovers_from_backup(self):
        self.tool_call("terminal", {}, session_id="sess-rec")
        self.mod._save_state(force=True)
        # Corrupt the primary state file; backup must be used on next load
        with open(self.mod._STATE_PATH, "w") as f:
            f.write("{corrupted json!!!")
        self.mod._state = None
        st = self.mod._load_state()["stats"]
        self.assertEqual(st["tools_used"]["terminal"], 1)

    def test_discord_notification_uses_rarity_embed(self):
        import json as _json
        captured = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured["data"] = req.data
            captured["url"] = req.full_url
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "123",
        }.get(key, fallback)
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            ach_def = self.mod.ACHIEVEMENT_DEFS["completionist"]  # legendary
            self.mod._send_discord_notification_sync(ach_def)
        finally:
            self.mod.urllib.request.urlopen = old

        payload = _json.loads(captured["data"])
        self.assertIn("embeds", payload)
        embed = payload["embeds"][0]
        self.assertEqual(embed["color"], self.mod._RARITY_COLORS["legendary"])
        self.assertIn("Completionist", embed["title"])

    def test_discord_notification_uses_thread_id(self):
        captured = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "111",
            "DISCORD_HOME_CHANNEL_THREAD_ID": "222",
        }.get(key, fallback)
        # The gateway session env may set an origin channel; drop it so the
        # home-thread target is the only one exercised
        old_origin = os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            self.mod._send_discord_notification_sync(self.mod.ACHIEVEMENT_DEFS["first_steps"])
        finally:
            self.mod.urllib.request.urlopen = old
            if old_origin is not None:
                os.environ["HERMES_SESSION_CHAT_ID"] = old_origin

        # The thread ID is the channel ID in the Discord API
        self.assertIn("/channels/222/messages", captured["url"])


class TestSessionEndStreaks(HookTestBase):
    """on_session_end daily-streak logic (Week Warrior / Monthly Master)."""

    def _set_last_active(self, days_ago):
        from datetime import date, timedelta
        self.stats()["last_active_date"] = (date.today() - timedelta(days=days_ago)).isoformat()  # noqa: DTZ011

    def test_first_session_starts_streak(self):
        self.mod._on_session_end(model="m1", platform="cli")
        self.assertEqual(self.stats()["current_streak"], 1)
        self.assertEqual(self.stats()["longest_streak"], 1)

    def test_same_day_does_not_double_count(self):
        self.mod._on_session_end(model="m1", platform="cli")
        self.mod._on_session_end(model="m1", platform="cli")
        self.assertEqual(self.stats()["current_streak"], 1)

    def test_yesterday_increments_streak(self):
        self.stats()["current_streak"] = 1
        self._set_last_active(1)
        self.mod._on_session_end(model="m1", platform="cli")
        self.assertEqual(self.stats()["current_streak"], 2)

    def test_gap_resets_streak(self):
        self.stats()["current_streak"] = 5
        self.stats()["longest_streak"] = 5
        self._set_last_active(3)
        self.mod._on_session_end(model="m1", platform="cli")
        self.assertEqual(self.stats()["current_streak"], 1)
        # longest_streak preserved
        self.assertEqual(self.stats()["longest_streak"], 5)

    def test_week_warrior_at_7_days(self):
        self.stats()["current_streak"] = 6
        self._set_last_active(1)
        self.mod._on_session_end(model="m1", platform="cli")
        self.assertTrue(self.unlocked("week_warrior"))
        self.assertFalse(self.unlocked("monthly_master"))

    def test_monthly_master_at_30_days(self):
        self.stats()["current_streak"] = 29
        self._set_last_active(1)
        self.mod._on_session_end(model="m1", platform="cli")
        self.assertTrue(self.unlocked("monthly_master"))

    def test_session_end_tracks_model_platform(self):
        self.mod._on_session_end(model="model-x", platform="telegram")
        st = self.stats()
        self.assertIn("model-x", st["models_used"])
        self.assertIn("telegram", st["platforms"])


class TestEnvLoading(HookTestBase):
    """_load_env_var reads from the Hermes .env file."""

    def test_loads_from_dotenv(self):
        env_path = os.path.join(self._tmp, ".env")
        with open(env_path, "w") as f:
            f.write("DISCORD_BOT_TOKEN=abc123\n")
            f.write('DISCORD_HOME_CHANNEL="999"\n')
        self.assertEqual(self.mod._load_env_var("DISCORD_BOT_TOKEN"), "abc123")
        self.assertEqual(self.mod._load_env_var("DISCORD_HOME_CHANNEL"), "999")
        self.assertEqual(self.mod._load_env_var("MISSING_KEY"), "")

    def test_ignores_comments_and_blank_lines(self):
        env_path = os.path.join(self._tmp, ".env")
        with open(env_path, "w") as f:
            f.write("# comment\n\nKEY=value\n")
        self.assertEqual(self.mod._load_env_var("KEY"), "value")


class TestRemainingGaps(HookTestBase):
    """Achievements that previously had no detection path at all."""

    def test_cli_champion_at_500(self):
        for i in range(500):
            self.tool_call("terminal", {"command": f"echo {i}"}, session_id="sess-cli")
        self.assertTrue(self.unlocked("shell_master"))
        self.assertTrue(self.unlocked("cli_champion"))

    def test_cron_master_and_overlord(self):
        for i in range(5):
            self.tool_call("cronjob", {"action": "create", "schedule": "every 2h"}, session_id="sess-c")
        self.assertTrue(self.unlocked("cron_master"))
        self.assertFalse(self.unlocked("cron_overlord"))
        for i in range(10):
            self.tool_call("cronjob", {"action": "create", "schedule": "every 2h"}, session_id="sess-c")
        self.assertTrue(self.unlocked("cron_overlord"))
        self.assertEqual(self.stats()["cron_jobs_created"], 15)

    def test_session_surfer(self):
        for _ in range(10):
            self.turn("resume with --continue")
        self.assertTrue(self.unlocked("session_surfer"))
        self.assertTrue(self.unlocked("session_sage"))
        self.assertEqual(self.stats()["session_resumes"], 10)


class TestPluginRegistration(unittest.TestCase):
    """register() wires all hooks and commands on a mock context."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ach-reg-")
        self.mod = _make_module(self._tmp)
        os.environ.pop("HERMES_HOME", None)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)

    def test_register_wires_hooks_and_commands(self):
        class Ctx:
            def __init__(self):
                self.commands = []
                self.hooks = []
            def register_command(self, name, handler, description="", args_hint=""):
                self.commands.append((name, handler))
            def register_hook(self, name, callback):
                self.hooks.append((name, callback))

        ctx = Ctx()
        self.mod.register(ctx)
        hook_names = {n for n, _ in ctx.hooks}
        self.assertEqual(hook_names,
                         {"post_llm_call", "post_tool_call", "on_session_start",
                          "on_session_end", "subagent_stop",
                          "post_approval_response", "on_session_reset"})
        cmd_names = {n for n, _ in ctx.commands}
        self.assertEqual(cmd_names, {"achievements", "achievement"})
        # Handlers are the real functions, not lambdas
        self.assertIs(ctx.hooks[0][1], self.mod._post_llm_call)


class TestCommandHandlers(HookTestBase):
    """Slash command output (achievements/achievement/lang)."""

    def test_achievements_list_all_groups(self):
        out = self.mod._handle_achievements("")
        for g in self.mod.GROUPS:
            self.assertIn(g, out)
        self.assertIn("Hermes Achievements", out)
        self.assertIn("0/13", out)  # Getting Started progress summary

    def test_achievements_list_under_discord_limit(self):
        # Discord caps messages at 2000 chars — the default view must fit
        out = self.mod._handle_achievements("")
        self.assertLessEqual(len(out), 2000, f"default view is {len(out)} chars")
        # And every group's full list must fit too
        for g in self.mod.GROUPS:
            slug = g.lower().replace(" & ", "_").replace(" ", "_")
            group_out = self.mod._handle_achievements(slug)
            self.assertLessEqual(len(group_out), 2000,
                                 f"group {g} view is {len(group_out)} chars")

    def test_achievements_stats_has_session_line(self):
        self.tool_call("terminal", {"command": "echo x"}, session_id="sess-stat")
        out = self.mod._handle_achievements("stats")
        self.assertIn("Unlocked:", out)
        self.assertIn("This session:", out)

    def test_achievements_stats_shows_new_hook_counters(self):
        # subagent_stop, approval responses, session resets all surface in stats
        for _ in range(3):
            self.mod._on_subagent_stop(
                parent_session_id="s", child_role="leaf",
                child_status="completed", duration_ms=1000,
            )
        self.mod._on_approval_response(
            command="cmd", description="d", pattern_key="k",
            session_key="s", surface="cli", choice="always",
        )
        self.mod._on_approval_response(
            command="cmd", description="d", pattern_key="k",
            session_key="s", surface="cli", choice="deny",
        )
        self.mod._on_session_reset(session_id="new", platform="discord")
        out = self.mod._handle_achievements("stats")
        self.assertIn("Subagents spawned:", out)
        self.assertIn("Permanent approvals:", out)
        self.assertIn("Approvals denied:", out)
        self.assertIn("Session resets:", out)
        # 3 children, not the legacy parallel_spawns counter
        self.assertIn("3", out)

    def test_achievements_group_filter(self):
        out = self.mod._handle_achievements("getting_started")
        self.assertIn("Getting Started", out)
        self.assertIn("First Steps", out)
        self.assertNotIn("Ghost in the Shell", out)

    def test_achievements_next_up(self):
        # 9/10 terminal commands → Terminal Jockey should be next up
        for _ in range(9):
            self.tool_call("terminal", {"command": "echo x"}, session_id="sess-next")
        out = self.mod._handle_achievements("next")
        self.assertIn("Next Up", out)
        self.assertIn("Terminal Jockey", out)
        self.assertIn("9/25", out)

    def test_achievements_next_empty(self):
        out = self.mod._handle_achievements("next")
        self.assertIn("No progress", out)

    def test_achievement_detail(self):
        out = self.mod._handle_achievement_detail("first_steps")
        self.assertIn("First Steps", out)
        self.assertIn("Common", out)
        self.assertIn("Getting Started", out)

    def test_achievement_detail_fuzzy(self):
        out = self.mod._handle_achievement_detail("steps")
        self.assertIn("First Steps", out)

    def test_achievement_detail_multiple_matches(self):
        out = self.mod._handle_achievement_detail("first")
        self.assertIn("Multiple:", out)

    def test_achievement_detail_unknown(self):
        out = self.mod._handle_achievement_detail("not_an_achievement")
        self.assertIn("Unknown", out)

    def test_lang_switch_and_show(self):
        out = self.mod._handle_lang("es")
        self.assertIn("Español", out)
        self.assertEqual(self.mod._load_state()["locale"], "es")
        # Achievements list now localized
        self.mod._unlock("first_steps")
        self.mod._state["newly_unlocked"] = []
        out2 = self.mod._handle_achievements("recent")
        self.assertNotIn("First Steps", out2)  # Spanish name differs
        self.mod._handle_lang("en")
        self.assertEqual(self.mod._load_state()["locale"], "en")


class TestReadmeSync(unittest.TestCase):
    """README achievement tables match ACHIEVEMENT_DEFS (no drift)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ach-readme-")
        self.mod = _make_module(self._tmp)
        os.environ.pop("HERMES_HOME", None)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("HERMES_HOME", None)

    def test_all_defs_in_readme(self):
        with open(os.path.join(PLUGIN_DIR, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for aid, adef in self.mod.ACHIEVEMENT_DEFS.items():
            self.assertIn(adef["name"], readme,
                          f"achievement '{adef['name']}' ({aid}) missing from README")

    def test_render_script_matches_readme(self):
        # Regenerate the table section in memory and compare with the file
        import subprocess
        import sys as _sys
        script = os.path.join(PLUGIN_DIR, "scripts", "render_readme.py")
        result = subprocess.run([_sys.executable, script], capture_output=True, text=True,
                                cwd=PLUGIN_DIR, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK: 100 achievements", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
