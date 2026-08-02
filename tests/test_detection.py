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
import re
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
        # Cancel any pending notification-debounce timer from this module
        try:
            if getattr(self.mod, "_notif_timer", None) is not None:
                self.mod._notif_timer.cancel()
                self.mod._notif_timer = None
            self.mod._NOTIF_QUEUE.clear()
        except Exception:  # noqa: BLE001, S110 — cleanup must never fail the test
            pass
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

    def test_skill_apprentice_at_5_installs(self):
        for _ in range(5):
            self.turn("hermes skills install web-search")
        self.assertTrue(self.unlocked("skill_apprentice"))
        self.assertEqual(self.stats()["skills_installed"], 5)

    def test_profile_collector_at_5_profiles(self):
        for _ in range(5):
            self.turn("hermes profile create work")
        self.assertTrue(self.unlocked("profile_collector"))
        self.assertTrue(self.unlocked("profile_juggler"))
        self.assertEqual(self.stats()["profiles_created"], 5)

    def test_mcp_networker_at_3_servers(self):
        for _ in range(3):
            self.turn("hermes mcp add my-server")
        self.assertTrue(self.unlocked("mcp_networker"))
        self.assertEqual(self.stats()["mcp_servers_connected"], 3)

    def test_yolo_champion_at_25(self):
        for _ in range(25):
            self.turn("hermes run --yolo task")
        self.assertTrue(self.unlocked("yolo_champion"))

    def test_skill_virtuoso_at_15_creations(self):
        for _ in range(15):
            self.tool_call("skill_manage", {"action": "create", "name": "x"})
        self.assertTrue(self.unlocked("skill_virtuoso"))
        self.assertTrue(self.unlocked("skill_artisan"))

    def test_skill_master_at_15_installs(self):
        for _ in range(15):
            self.turn("hermes skills install web-search")
        self.assertTrue(self.unlocked("skill_master"))
        self.assertTrue(self.unlocked("skill_apprentice"))

    def test_threshold_check_empty_counts_noop(self):
        self.mod._check_tool_usage_thresholds({}, "2026-01-01T00:00:00")
        # No crash; nothing unlocked
        self.assertFalse(self.unlocked("terminal_jockey"))

    def test_count_user_commands_ignores_non_strings(self):
        self.mod._count_user_commands([123, None, {"a": 1}], self.stats(),
                                      "2026-01-01T00:00:00")
        self.assertEqual(self.stats().get("config_changes", 0), 0)


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

    def test_redelivery_of_same_session_counts_once(self):
        # A gateway crash-recovery retry re-fires on_session_start with
        # the SAME session_id — the counter must not inflate.
        for _ in range(3):
            self.mod._on_session_start(session_id="s1")
        self.assertEqual(self.stats()["total_sessions"], 1)
        # A genuinely new session still counts afterwards
        self.mod._on_session_start(session_id="s2")
        self.assertEqual(self.stats()["total_sessions"], 2)

    def test_interleaved_ids_count_each_distinct_session(self):
        # Duplicate of an EARLIER (non-consecutive) id is impossible in
        # real gateways, but the guard must not undercount legitimately
        # distinct sessions appearing back-to-back.
        for sid in ("s1", "s1", "s2", "s2", "s3", "s3"):
            self.mod._on_session_start(session_id=sid)
        self.assertEqual(self.stats()["total_sessions"], 3)

    def test_missing_session_id_falls_back_to_counting_every_firing(self):
        # Gateways without session ids (and synthetic callers) keep the
        # legacy behavior: every firing counts.
        for _ in range(3):
            self.mod._on_session_start()
        self.assertEqual(self.stats()["total_sessions"], 3)
        # Falsy ids behave the same as missing ids
        self.mod._on_session_start(session_id="")
        self.mod._on_session_start(session_id=None)
        self.assertEqual(self.stats()["total_sessions"], 5)


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


class TestApprovalContext(HookTestBase):
    """post_approval_response surface + pattern_keys: remote approvals
    and danger-class diversity — the two kwargs the old handler ignored."""

    def test_gateway_surface_unlocks_remote_warden(self):
        self.mod._on_approval_response(
            command="rm -rf /tmp/x", description="dangerous",
            pattern_key="rm_rf", pattern_keys=["rm_rf"],
            session_key="s", surface="gateway", choice="once",
        )
        self.assertTrue(self.unlocked("remote_warden"))
        self.assertEqual(self.stats()["approvals_gateway"], 1)
        # CLI approvals are NOT remote — no unlock.
        self.mod._on_approval_response(
            command="chmod 777 /tmp/x", description="dangerous",
            pattern_key="chmod", pattern_keys=["chmod"],
            session_key="s", surface="cli", choice="once",
        )
        self.assertEqual(self.stats()["approvals_gateway"], 1)
        self.assertFalse(self.unlocked("long_distance_operator"))

    def test_ten_gateway_approvals_unlock_long_distance(self):
        for i in range(10):
            self.mod._on_approval_response(
                command=f"cmd {i}", description="d", pattern_key=f"k{i}",
                pattern_keys=[f"k{i}"], session_key="s",
                surface="gateway", choice="once",
            )
        self.assertTrue(self.unlocked("long_distance_operator"))

    def test_deny_does_not_count_surface(self):
        self.mod._on_approval_response(
            command="rm -rf /", description="dangerous", pattern_key="rm",
            pattern_keys=["rm"], session_key="s", surface="gateway",
            choice="deny",
        )
        self.assertEqual(self.stats()["approvals_gateway"], 0)
        self.assertFalse(self.unlocked("remote_warden"))

    def test_timeout_does_not_count_surface(self):
        self.mod._on_approval_response(
            command="rm -rf /", description="dangerous", pattern_key="rm",
            pattern_keys=["rm"], session_key="s", surface="gateway",
            choice="timeout",
        )
        self.assertEqual(self.stats()["approvals_gateway"], 0)
        self.assertFalse(self.unlocked("remote_warden"))

    def test_pattern_diversity_unlocks_risk_explorer(self):
        for i in range(5):
            self.mod._on_approval_response(
                command=f"cmd {i}", description="d", pattern_key=f"k{i}",
                pattern_keys=[f"k{i}"], session_key="s",
                surface="cli", choice="once",
            )
        self.assertTrue(self.unlocked("risk_explorer"))
        self.assertEqual(len(self.stats()["approved_patterns"]), 5)

    def test_repeated_same_class_adds_nothing(self):
        for _ in range(10):
            self.mod._on_approval_response(
                command="cmd", description="d", pattern_key="same",
                pattern_keys=["same"], session_key="s",
                surface="cli", choice="once",
            )
        self.assertFalse(self.unlocked("risk_explorer"))
        self.assertEqual(len(self.stats()["approved_patterns"]), 1)

    def test_multi_key_command_counts_all_classes(self):
        self.mod._on_approval_response(
            command="cmd", description="d", pattern_key="a",
            pattern_keys=["a", "b", "c"], session_key="s",
            surface="cli", choice="once",
        )
        self.assertEqual(len(self.stats()["approved_patterns"]), 3)

    def test_pattern_diversity_cascades_to_25(self):
        for i in range(25):
            self.mod._on_approval_response(
                command=f"cmd {i}", description="d", pattern_key=f"k{i}",
                pattern_keys=[f"k{i}"], session_key="s",
                surface="cli", choice="once",
            )
        self.assertTrue(self.unlocked("risk_explorer"))
        self.assertTrue(self.unlocked("danger_collector"))
        self.assertTrue(self.unlocked("living_on_the_edge"))

    def test_lower_tier_unlocks_when_higher_reached(self):
        # Jump straight to 15 distinct classes — all three must unlock
        # (non-elif cascade).
        for i in range(15):
            self.mod._on_approval_response(
                command=f"cmd {i}", description="d", pattern_key=f"k{i}",
                pattern_keys=[f"k{i}"], session_key="s",
                surface="cli", choice="once",
            )
        self.assertTrue(self.unlocked("risk_explorer"))
        self.assertTrue(self.unlocked("danger_collector"))
        self.assertFalse(self.unlocked("living_on_the_edge"))

    def test_pattern_keys_absent_is_safe(self):
        self.mod._on_approval_response(
            command="cmd", description="d", pattern_key="k",
            session_key="s", surface="gateway", choice="once",
        )
        self.assertTrue(self.unlocked("remote_warden"))
        self.assertFalse(self.unlocked("risk_explorer"))

    def test_persisted_list_is_normalized_to_set(self):
        # Simulate a state.json written before v2.17.0: approved_patterns
        # persisted as a JSON list, not a set. The handler must normalize.
        self.mod._load_state()
        self.mod._state["stats"]["approved_patterns"] = ["a", "b"]
        self.mod._on_approval_response(
            command="cmd", description="d", pattern_key="c",
            pattern_keys=["c"], session_key="s",
            surface="cli", choice="once",
        )
        self.assertEqual(len(self.stats()["approved_patterns"]), 3)


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


class TestSessionFinalize(HookTestBase):
    """on_session_finalize: shutdown flush of state + queued notifications."""

    def test_finalize_forces_state_save(self):
        # Debounced save may be pending — finalize must force it to disk
        self.tool_call("terminal", {}, session_id="sess-fin")
        # Simulate pending debounce: don't call _save_state, just finalize
        self.mod._on_session_finalize(session_id="sess-fin", platform="gateway")
        self.assertTrue(os.path.exists(self.mod._STATE_PATH))
        import json as _json
        with open(self.mod._STATE_PATH) as f:
            saved = _json.load(f)
        self.assertEqual(saved["stats"]["tools_used"]["terminal"], 1)

    def test_finalize_flushes_queued_notifications(self):
        # Queued unlocks not yet delivered (debounce window open) → flushed
        captured = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured.append(req.data)
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "456",
        }.get(key, fallback)
        old_origin = os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS["first_steps"])
            # Timer still pending — finalize delivers synchronously
            self.mod._on_session_finalize(session_id="s", platform="gateway")
        finally:
            self.mod.urllib.request.urlopen = old
            if old_origin is not None:
                os.environ["HERMES_SESSION_CHAT_ID"] = old_origin
        self.assertEqual(len(captured), 1)

    def test_notification_sends_user_agent_header(self):
        # Cloudflare (Discord's CDN) rejects API calls without a browser-like
        # User-Agent with HTTP 403 error code 1010 — a missing UA silently
        # kills every notification. Regression guard: the request must carry
        # both the bot Authorization and a User-Agent.
        captured = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured.append(req)
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "456",
        }.get(key, fallback)
        old_origin = os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS["first_steps"])
            self.mod._on_session_finalize(session_id="s", platform="gateway")
        finally:
            self.mod.urllib.request.urlopen = old
            if old_origin is not None:
                os.environ["HERMES_SESSION_CHAT_ID"] = old_origin
        self.assertEqual(len(captured), 1)
        req = captured[0]
        # urllib normalizes header names to Title-Case ('User-Agent' →
        # 'User-agent'), so match case-insensitively.
        headers = {k.lower(): v for k, v in req.headers.items()}
        auth = headers.get("authorization", "")
        ua = headers.get("user-agent", "")
        self.assertTrue(auth.startswith("Bot "), f"missing bot Authorization: {auth!r}")
        self.assertTrue(len(ua) > 5, f"missing User-Agent: {ua!r}")

    def test_finalize_never_crashes(self):
        # Broken state path + broken notification → finalize still returns
        old_path, old_bak = self.mod._STATE_PATH, self.mod._STATE_BAK_PATH
        self.mod._STATE_PATH = "/proc/definitely/not/writable/state.json"
        self.mod._STATE_BAK_PATH = "/proc/definitely/not/writable/state.json.bak"
        try:
            self.mod._on_session_finalize(session_id="s", platform="gateway")
        finally:
            self.mod._STATE_PATH, self.mod._STATE_BAK_PATH = old_path, old_bak

    def test_finalize_before_any_state_is_noop(self):
        # Gateway can finalize before the first hook ever fires — no crash,
        # no spurious state file
        self.mod._state = None
        self.mod._on_session_finalize(session_id="s", platform="gateway")
        self.assertFalse(os.path.exists(self.mod._STATE_PATH))

    def test_finalize_swallows_save_and_flush_errors(self):
        # Even if _save_state and _flush_notification_queue raise, finalize
        # must return without crashing shutdown
        old_save = self.mod._save_state
        old_flush = self.mod._flush_notification_queue

        def boom_save(force=False):
            raise RuntimeError("state write failed")

        def boom_flush():
            raise RuntimeError("notify failed")

        self.mod._save_state = boom_save
        self.mod._flush_notification_queue = boom_flush
        try:
            self.mod._on_session_finalize(session_id="s", platform="gateway")
        finally:
            self.mod._save_state = old_save
            self.mod._flush_notification_queue = old_flush


class TestSubagentStart(HookTestBase):
    """subagent_start: true concurrency tracking drives Conductor."""

    def test_peak_concurrency_unlocks_conductor(self):
        # 3 children alive at once (start x3 before any stop)
        for _ in range(3):
            self.mod._on_subagent_start(
                parent_session_id="s", child_session_id="c",
                child_role="leaf", child_goal="task",
            )
        self.assertTrue(self.unlocked("conductor"))
        self.assertEqual(self.stats()["concurrent_subagents"], 3)
        self.assertEqual(self.stats()["max_concurrent_subagents"], 3)

    def test_concurrency_progress_before_threshold(self):
        self.mod._on_subagent_start(
            parent_session_id="s", child_session_id="c",
            child_role="leaf", child_goal="task",
        )
        self.assertFalse(self.unlocked("conductor"))
        st = self.mod._load_state()["achievements"]["conductor"]
        self.assertEqual(st["progress"]["current"], 1)
        self.assertEqual(st["progress"]["target"], 3)

    def test_stop_decrements_concurrency(self):
        self.mod._on_subagent_start(child_session_id="c1")
        self.mod._on_subagent_start(child_session_id="c2")
        self.assertEqual(self.stats()["concurrent_subagents"], 2)
        self.mod._on_subagent_stop(
            parent_session_id="s", child_role="leaf",
            child_status="completed", duration_ms=1000,
        )
        self.assertEqual(self.stats()["concurrent_subagents"], 1)
        self.assertEqual(self.stats()["max_concurrent_subagents"], 2)

    def test_concurrency_never_goes_negative(self):
        # stop without a matching start (plugin loaded mid-run)
        for _ in range(3):
            self.mod._on_subagent_stop(
                parent_session_id="s", child_role="leaf",
                child_status="completed", duration_ms=1000,
            )
        self.assertEqual(self.stats()["concurrent_subagents"], 0)

    def test_start_stop_cycle_keeps_peak(self):
        # one at a time, peak stays 1 — no Conductor
        for _ in range(5):
            self.mod._on_subagent_start(child_session_id="c")
            self.mod._on_subagent_stop(
                parent_session_id="s", child_role="leaf",
                child_status="completed", duration_ms=1000,
            )
        self.assertFalse(self.unlocked("conductor"))
        self.assertEqual(self.stats()["max_concurrent_subagents"], 1)


class TestApiRequestError(HookTestBase):
    """api_request_error: surviving LLM API errors drives Indestructible."""

    def test_ten_errors_unlock_indestructible(self):
        for _ in range(10):
            self.mod._on_api_request_error(
                error_type="InvalidAPIResponse", error_message="bad",
                status_code=500, retry_count=3, retryable=True,
            )
        self.assertTrue(self.unlocked("indestructible"))
        self.assertEqual(self.stats()["api_errors"], 10)

    def test_progress_before_threshold(self):
        for _ in range(3):
            self.mod._on_api_request_error(
                error_type="Timeout", error_message="slow",
                status_code=429, retry_count=1, retryable=True,
            )
        self.assertFalse(self.unlocked("indestructible"))
        st = self.mod._load_state()["achievements"]["indestructible"]
        self.assertEqual(st["progress"]["current"], 3)
        self.assertEqual(st["progress"]["target"], 10)

    def test_single_error_counts(self):
        self.mod._on_api_request_error(
            error_type="RateLimit", error_message="429",
            status_code=429, retry_count=0, retryable=False,
        )
        self.assertEqual(self.stats()["api_errors"], 1)


class TestRetryDepth(HookTestBase):
    """api_request_error retry_count: sustained-failure resilience.

    retry_count is the number of consecutive failures the SAME request
    survived before the hook fired (the gateway retry loop fires the hook
    on every failed attempt, incrementing depth). Distinct from api_errors
    breadth: three requests failing once each never reach depth 2.
    """

    def _error(self, retry_count):
        self.mod._on_api_request_error(
            error_type="Timeout", error_message="slow",
            status_code=500, retry_count=retry_count, retryable=True,
        )

    def test_depth_zero_no_retry_achievements(self):
        self._error(0)
        self.assertFalse(self.unlocked("tenacious"))
        self.assertFalse(self.unlocked("undeterred"))
        self.assertEqual(self.stats().get("max_retry_depth", 0), 0)

    def test_depth_one_sets_progress(self):
        self._error(1)
        self.assertFalse(self.unlocked("tenacious"))
        st = self.mod._load_state()["achievements"]["tenacious"]
        self.assertEqual(st["progress"]["current"], 1)
        self.assertEqual(st["progress"]["target"], 2)
        self.assertEqual(self.stats()["max_retry_depth"], 1)

    def test_depth_two_unlocks_tenacious(self):
        self._error(2)
        self.assertTrue(self.unlocked("tenacious"))
        self.assertFalse(self.unlocked("undeterred"))
        self.assertEqual(self.stats()["max_retry_depth"], 2)

    def test_depth_four_unlocks_both(self):
        self._error(4)
        self.assertTrue(self.unlocked("tenacious"))
        self.assertTrue(self.unlocked("undeterred"))
        self.assertEqual(self.stats()["max_retry_depth"], 4)

    def test_max_depth_keeps_deepest(self):
        self._error(1)
        self._error(3)
        self._error(2)
        self.assertEqual(self.stats()["max_retry_depth"], 3)

    def test_shallow_errors_never_reach_depth(self):
        # Breadth without depth: 10 single failures ≠ sustained outage.
        for _ in range(10):
            self._error(0)
        self.assertFalse(self.unlocked("tenacious"))
        self.assertFalse(self.unlocked("undeterred"))
        self.assertEqual(self.stats().get("max_retry_depth", 0), 0)

    def test_bad_retry_count_ignored(self):
        self.mod._on_api_request_error(
            error_type="Timeout", error_message="x",
            status_code=500, retry_count="garbage", retryable=True,
        )
        self.assertEqual(self.stats().get("max_retry_depth", 0), 0)
        self.assertFalse(self.unlocked("tenacious"))


class TestPostApiRequest(HookTestBase):
    """post_api_request: tokens, fast responses, providers, context depth."""

    def test_deep_context_at_50_messages(self):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=3.0,
            model="m1", provider="p1", api_call_count=1,
            message_count=50,
        )
        self.assertTrue(self.unlocked("deep_context"))
        self.assertFalse(self.unlocked("context_colossus"))
        self.assertEqual(self.stats()["peak_context_messages"], 50)

    def test_context_colossus_at_100_messages(self):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=3.0,
            model="m1", provider="p1", api_call_count=1,
            message_count=100,
        )
        self.assertTrue(self.unlocked("deep_context"))
        self.assertTrue(self.unlocked("context_colossus"))
        self.assertEqual(self.stats()["peak_context_messages"], 100)

    def test_deep_context_progress_before_threshold(self):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=3.0,
            model="m1", provider="p1", api_call_count=1,
            message_count=20,
        )
        self.assertFalse(self.unlocked("deep_context"))
        st = self.mod._load_state()["achievements"]["deep_context"]
        self.assertEqual(st["progress"]["current"], 20)
        self.assertEqual(st["progress"]["target"], 50)

    def test_peak_context_keeps_max(self):
        self.mod._post_api_request(
            usage={}, api_duration=3.0, model="m1", provider="p1",
            api_call_count=1, message_count=30,
        )
        self.mod._post_api_request(
            usage={}, api_duration=3.0, model="m1", provider="p1",
            api_call_count=1, message_count=80,
        )
        self.mod._post_api_request(
            usage={}, api_duration=3.0, model="m1", provider="p1",
            api_call_count=1, message_count=10,
        )
        self.assertEqual(self.stats()["peak_context_messages"], 80)

    def test_missing_message_count_is_noop(self):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=3.0,
            model="m1", provider="p1", api_call_count=1,
        )
        self.assertFalse(self.unlocked("deep_context"))
        self.assertNotIn("peak_context_messages", self.stats())

    def test_zero_message_count_ignored(self):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=3.0,
            model="m1", provider="p1", api_call_count=1,
            message_count=0,
        )
        self.assertFalse(self.unlocked("deep_context"))


class TestPreApiRequest(HookTestBase):
    """pre_api_request: local endpoints + single-request input-token spikes."""

    def test_local_first_on_localhost(self):
        self.mod._pre_api_request(
            base_url="http://localhost:4000/v1", approx_input_tokens=1000,
            model="m1", provider="p1", api_call_count=1,
        )
        self.assertTrue(self.unlocked("local_first"))
        self.assertFalse(self.unlocked("self_hosted"))
        self.assertEqual(self.stats()["local_requests"], 1)

    def test_local_first_private_ip_and_suffixes(self):
        for url in ("http://192.168.1.10:8080/v1", "http://10.0.0.5:8000",
                    "http://172.16.0.2:9000", "http://ollama.local:11434",
                    "http://127.0.0.1:11434/v1"):
            self.mod._pre_api_request(
                base_url=url, approx_input_tokens=100, model="m1",
                provider="p1", api_call_count=1,
            )
        self.assertTrue(self.unlocked("local_first"))
        self.assertEqual(self.stats()["local_requests"], 5)

    def test_cloud_urls_do_not_count_local(self):
        self.mod._pre_api_request(
            base_url="https://api.openai.com/v1", approx_input_tokens=100,
            model="m1", provider="p1", api_call_count=1,
        )
        self.mod._pre_api_request(
            base_url="https://api.anthropic.com/v1", approx_input_tokens=100,
            model="m1", provider="p1", api_call_count=1,
        )
        self.assertFalse(self.unlocked("local_first"))
        self.assertNotIn("local_requests", self.stats())

    def test_self_hosted_at_25_requests(self):
        for i in range(25):
            self.mod._pre_api_request(
                base_url="http://localhost:4000/v1", approx_input_tokens=100,
                model="m1", provider="p1", api_call_count=1,
            )
        self.assertTrue(self.unlocked("local_first"))
        self.assertTrue(self.unlocked("self_hosted"))
        self.assertEqual(self.stats()["local_requests"], 25)

    def test_self_hosted_progress_before_threshold(self):
        for _ in range(5):
            self.mod._pre_api_request(
                base_url="http://localhost:4000/v1", approx_input_tokens=100,
                model="m1", provider="p1", api_call_count=1,
            )
        self.assertFalse(self.unlocked("self_hosted"))
        st = self.mod._load_state()["achievements"]["self_hosted"]
        self.assertEqual(st["progress"]["current"], 5)
        self.assertEqual(st["progress"]["target"], 25)

    def test_context_monster_at_200k(self):
        self.mod._pre_api_request(
            base_url="https://api.openai.com/v1", approx_input_tokens=200_000,
            model="m1", provider="p1", api_call_count=1,
        )
        self.assertTrue(self.unlocked("context_monster"))
        self.assertFalse(self.unlocked("token_tsunami"))
        self.assertEqual(self.stats()["peak_input_tokens"], 200_000)

    def test_token_tsunami_at_500k(self):
        self.mod._pre_api_request(
            base_url="https://api.openai.com/v1", approx_input_tokens=500_000,
            model="m1", provider="p1", api_call_count=1,
        )
        self.assertTrue(self.unlocked("context_monster"))
        self.assertTrue(self.unlocked("token_tsunami"))
        self.assertEqual(self.stats()["peak_input_tokens"], 500_000)

    def test_peak_input_keeps_max(self):
        for n in (1000, 90_000, 250_000, 50_000):
            self.mod._pre_api_request(
                base_url="https://api.openai.com/v1", approx_input_tokens=n,
                model="m1", provider="p1", api_call_count=1,
            )
        self.assertEqual(self.stats()["peak_input_tokens"], 250_000)
        self.assertTrue(self.unlocked("context_monster"))

    def test_missing_approx_is_noop(self):
        self.mod._pre_api_request(
            base_url="https://api.openai.com/v1", model="m1",
            provider="p1", api_call_count=1,
        )
        self.assertFalse(self.unlocked("context_monster"))
        self.assertNotIn("peak_input_tokens", self.stats())

    def test_invalid_base_url_noop(self):
        self.mod._pre_api_request(
            base_url="not a url", approx_input_tokens=100,
            model="m1", provider="p1", api_call_count=1,
        )
        self.assertFalse(self.unlocked("local_first"))
        self.assertNotIn("local_requests", self.stats())


class TestPreLlmCall(HookTestBase):
    """pre_llm_call: fresh-conversation counting via is_first_turn."""

    def test_icebreaker_on_first_fresh_context(self):
        self.mod._pre_llm_call(
            is_first_turn=True, session_id="s1", task_id="t1",
            turn_id="t", user_message="hi", conversation_history=[],
            model="m1", platform="cli",
        )
        self.assertTrue(self.unlocked("icebreaker"))
        self.assertEqual(self.stats()["conversations_started"], 1)

    def test_not_first_turn_is_noop(self):
        self.mod._pre_llm_call(
            is_first_turn=False, session_id="s1", task_id="t1",
            turn_id="t", user_message="hi", conversation_history=[],
            model="m1", platform="cli",
        )
        self.assertFalse(self.unlocked("icebreaker"))
        self.assertEqual(self.stats().get("conversations_started", 0), 0)

    def test_missing_flag_is_noop(self):
        self.mod._pre_llm_call(session_id="s1", user_message="hi")
        self.assertFalse(self.unlocked("icebreaker"))
        self.assertEqual(self.stats().get("conversations_started", 0), 0)

    def test_conversation_habit_at_10(self):
        for i in range(10):
            self.mod._pre_llm_call(
                is_first_turn=True, session_id=f"s{i}", task_id="t",
                turn_id="t", user_message="hi", conversation_history=[],
                model="m1", platform="cli",
            )
        self.assertTrue(self.unlocked("icebreaker"))
        self.assertTrue(self.unlocked("conversation_habit"))
        self.assertFalse(self.unlocked("serial_starter"))
        self.assertFalse(self.unlocked("conversation_colossus"))
        self.assertEqual(self.stats()["conversations_started"], 10)

    def test_serial_starter_at_50(self):
        for i in range(50):
            self.mod._pre_llm_call(
                is_first_turn=True, session_id=f"s{i}", task_id="t",
                turn_id="t", user_message="hi", conversation_history=[],
                model="m1", platform="cli",
            )
        self.assertTrue(self.unlocked("serial_starter"))
        self.assertFalse(self.unlocked("conversation_colossus"))

    def test_colossus_at_100(self):
        for i in range(100):
            self.mod._pre_llm_call(
                is_first_turn=True, session_id=f"s{i}", task_id="t",
                turn_id="t", user_message="hi", conversation_history=[],
                model="m1", platform="cli",
            )
        self.assertTrue(self.unlocked("conversation_colossus"))
        self.assertEqual(self.stats()["conversations_started"], 100)

    def test_progress_tracks_current_count(self):
        self.mod._pre_llm_call(
            is_first_turn=True, session_id="s1", task_id="t",
            turn_id="t", user_message="hi", conversation_history=[],
            model="m1", platform="cli",
        )
        st = self.mod._load_state()["achievements"]["conversation_habit"]
        self.assertEqual(st["progress"]["current"], 1)
        self.assertEqual(st["progress"]["target"], 10)


class TestPreToolCall(HookTestBase):
    """pre_tool_call: single-response tool batching via api_request_id."""

    def _fire(self, req_id, tool="terminal"):
        self.mod._pre_tool_call(
            tool_name=tool, args={}, task_id="t", session_id="s",
            tool_call_id="tc", turn_id="turn", api_request_id=req_id,
        )

    def test_double_time_at_two_calls_same_response(self):
        self._fire("api-1")
        self.assertFalse(self.unlocked("double_time"))
        self._fire("api-1")
        self.assertTrue(self.unlocked("double_time"))
        self.assertEqual(self.stats()["peak_tools_per_response"], 2)

    def test_single_call_no_unlock(self):
        self._fire("api-1")
        self.assertFalse(self.unlocked("double_time"))
        self.assertEqual(self.stats()["peak_tools_per_response"], 1)

    def test_new_response_resets_batch(self):
        # 2 calls across DIFFERENT responses must NOT count as a batch
        self._fire("api-1")
        self._fire("api-2")
        self.assertFalse(self.unlocked("double_time"))
        self.assertEqual(self.stats()["peak_tools_per_response"], 1)

    def test_missing_api_request_id_is_noop(self):
        self.mod._pre_tool_call(tool_name="terminal", args={})
        self.assertFalse(self.unlocked("double_time"))
        self.assertEqual(self.stats().get("peak_tools_per_response", 0), 0)

    def test_batch_artist_at_5(self):
        for _ in range(5):
            self._fire("api-1")
        self.assertTrue(self.unlocked("double_time"))
        self.assertTrue(self.unlocked("batch_artist"))
        self.assertFalse(self.unlocked("parallel_barrage"))

    def test_parallel_barrage_at_10(self):
        for _ in range(10):
            self._fire("api-1")
        self.assertTrue(self.unlocked("parallel_barrage"))
        self.assertFalse(self.unlocked("tool_torrent"))

    def test_tool_torrent_at_20(self):
        for _ in range(20):
            self._fire("api-1")
        self.assertTrue(self.unlocked("tool_torrent"))
        self.assertEqual(self.stats()["peak_tools_per_response"], 20)

    def test_peak_keeps_max_across_responses(self):
        for _ in range(3):
            self._fire("api-small")
        for _ in range(12):
            self._fire("api-big")
        self.assertEqual(self.stats()["peak_tools_per_response"], 12)

    def test_progress_tracks_current_batch(self):
        self._fire("api-1")
        self._fire("api-1")
        st = self.mod._load_state()["achievements"]["batch_artist"]
        self.assertEqual(st["progress"]["current"], 2)
        self.assertEqual(st["progress"]["target"], 5)


class TestTransformTerminalOutput(HookTestBase):
    """transform_terminal_output: raw output volume, env diversity, exit codes."""

    def _fire(self, output="", env_type="local", returncode=0):
        self.mod._transform_terminal_output(
            command="ls", output=output, returncode=returncode,
            task_id="t", env_type=env_type,
        )

    def test_never_transforms_output(self):
        """Observer contract: the hook must return None, never a string."""
        result = self.mod._transform_terminal_output(
            command="ls", output="hello world", returncode=0,
            task_id="t", env_type="local",
        )
        self.assertIsNone(result)

    def test_small_output_no_unlock(self):
        self._fire(output="x" * 100)
        self.assertFalse(self.unlocked("verbose_output"))
        self.assertFalse(self.unlocked("data_flood"))
        self.assertEqual(self.stats().get("peak_terminal_output_bytes", 0), 100)

    def test_verbose_output_at_100kb(self):
        self._fire(output="x" * (100 * 1024))
        self.assertTrue(self.unlocked("verbose_output"))
        self.assertFalse(self.unlocked("data_flood"))

    def test_data_flood_at_1mb(self):
        self._fire(output="x" * (1024 * 1024))
        self.assertTrue(self.unlocked("verbose_output"))
        self.assertTrue(self.unlocked("data_flood"))

    def test_peak_keeps_max(self):
        self._fire(output="x" * 100)
        self._fire(output="x" * 5000)
        self.assertEqual(self.stats()["peak_terminal_output_bytes"], 5000)

    def test_ghost_command_at_exit_127(self):
        self._fire(returncode=0)
        self.assertFalse(self.unlocked("ghost_command"))
        self._fire(returncode=127)
        self.assertTrue(self.unlocked("ghost_command"))

    def test_other_exit_codes_no_unlock(self):
        for code in (1, 2, 126, 255):
            self._fire(returncode=code)
        self.assertFalse(self.unlocked("ghost_command"))

    def test_env_diversity_multi_env_at_2(self):
        self._fire(env_type="local")
        self.assertFalse(self.unlocked("multi_env"))
        self._fire(env_type="docker")
        self.assertTrue(self.unlocked("multi_env"))
        self.assertFalse(self.unlocked("omnipresent"))

    def test_env_diversity_omnipresent_at_5(self):
        for env in ("local", "ssh", "docker", "singularity", "modal"):
            self._fire(env_type=env)
        self.assertTrue(self.unlocked("multi_env"))
        self.assertTrue(self.unlocked("omnipresent"))

    def test_repeat_env_not_counted_twice(self):
        for _ in range(5):
            self._fire(env_type="local")
        self.assertFalse(self.unlocked("multi_env"))
        self.assertEqual(len(self.stats().get("env_types", set())), 1)

    def test_env_progress_tracks_current(self):
        self._fire(env_type="local")
        st = self.mod._load_state()["achievements"]["multi_env"]
        self.assertEqual(st["progress"]["current"], 1)
        self.assertEqual(st["progress"]["target"], 2)

    def test_envs_persist_as_list(self):
        """env_types set must be JSON-safe after save (sorted list)."""
        self._fire(env_type="local")
        self._fire(env_type="docker")
        self.mod._save_state(force=True)
        import json
        with open(self.mod._STATE_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["stats"]["env_types"], ["docker", "local"])


class TestTransformToolResult(HookTestBase):
    """transform_tool_result: full result size / context bloat."""

    def _fire(self, result="", tool="search_files"):
        self.mod._transform_tool_result(
            tool_name=tool, args={}, result=result,
            task_id="t", session_id="s", tool_call_id="tc",
            turn_id="turn", api_request_id="api-1", duration_ms=100,
            status="ok", error_type=None, error_message=None,
        )

    def test_never_transforms_result(self):
        """Observer contract: the hook must return None, never a string."""
        result = self.mod._transform_tool_result(
            tool_name="terminal", args={}, result="data",
            task_id="t", session_id="s", tool_call_id="tc",
            turn_id="turn", api_request_id="api-1", duration_ms=100,
            status="ok", error_type=None, error_message=None,
        )
        self.assertIsNone(result)

    def test_small_result_no_unlock(self):
        self._fire(result="y" * 500)
        self.assertFalse(self.unlocked("big_haul"))
        self.assertFalse(self.unlocked("colossal_result"))
        self.assertEqual(self.stats().get("peak_tool_result_bytes", 0), 500)

    def test_big_haul_at_1mb(self):
        self._fire(result="y" * (1024 * 1024))
        self.assertTrue(self.unlocked("big_haul"))
        self.assertFalse(self.unlocked("colossal_result"))

    def test_colossal_result_at_10mb(self):
        self._fire(result="y" * (10 * 1024 * 1024))
        self.assertTrue(self.unlocked("big_haul"))
        self.assertTrue(self.unlocked("colossal_result"))

    def test_peak_keeps_max(self):
        self._fire(result="y" * 100)
        self._fire(result="y" * 9000)
        self.assertEqual(self.stats()["peak_tool_result_bytes"], 9000)

    def test_progress_tracks_current(self):
        self._fire(result="y" * 100)
        st = self.mod._load_state()["achievements"]["big_haul"]
        self.assertEqual(st["progress"]["current"], 100)
        self.assertEqual(st["progress"]["target"], 1024 * 1024)

    def test_empty_result_noop(self):
        self._fire(result="")
        self.assertEqual(self.stats().get("peak_tool_result_bytes", 0), 0)
        self.assertFalse(self.unlocked("big_haul"))


class TestModelResponseVerbosity(HookTestBase):
    """post_llm_call assistant_response: model-output word count."""

    def _turn(self, response="hi"):
        self.mod._post_llm_call(
            user_message="hello",
            assistant_response=response,
            conversation_history=[
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": response},
            ],
            model="m1", platform="cli",
        )

    def test_short_response_no_unlock(self):
        self._turn(response="short reply")
        self.assertFalse(self.unlocked("essayist"))
        self.assertFalse(self.unlocked("novel_author"))
        self.assertEqual(self.stats().get("longest_response_words", 0), 2)

    def test_essayist_at_1000_words(self):
        self._turn(response=("word " * 1000).strip())
        self.assertTrue(self.unlocked("essayist"))
        self.assertFalse(self.unlocked("novel_author"))

    def test_novel_author_at_5000_words(self):
        self._turn(response=("word " * 5000).strip())
        self.assertTrue(self.unlocked("essayist"))
        self.assertTrue(self.unlocked("novel_author"))

    def test_peak_keeps_max(self):
        self._turn(response="a")
        self._turn(response=("word " * 1200).strip())
        self.assertEqual(self.stats()["longest_response_words"], 1200)

    def test_missing_response_noop(self):
        self.mod._post_llm_call(
            user_message="hello", conversation_history=[],
            model="m1", platform="cli",
        )
        self.assertEqual(self.stats().get("longest_response_words", 0), 0)

    def test_progress_tracks_current(self):
        self._turn(response=("word " * 250).strip())
        st = self.mod._load_state()["achievements"]["essayist"]
        self.assertEqual(st["progress"]["current"], 250)
        self.assertEqual(st["progress"]["target"], 1000)

    def test_user_verbosity_does_not_unlock_model_achievements(self):
        # A long USER message must NOT unlock essayist (that's the model's
        # own response) — the two dimensions are strictly separated.
        self.mod._post_llm_call(
            user_message=("word " * 2000).strip(),
            assistant_response="short",
            conversation_history=[{"role": "user", "content": "x"}],
            model="m1", platform="cli",
        )
        self.assertFalse(self.unlocked("essayist"))


class TestTruncation(HookTestBase):
    """post_api_request finish_reason=length: output-cap hits."""

    def _fire(self, finish_reason="stop"):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=1.0,
            model="m1", provider="p1", api_call_count=1,
            message_count=10, finish_reason=finish_reason,
        )

    def test_stop_no_unlock(self):
        self._fire("stop")
        self._fire("tool_calls")
        self.assertFalse(self.unlocked("cut_short"))
        self.assertEqual(self.stats().get("truncated_responses", 0), 0)

    def test_cut_short_on_first_length(self):
        self._fire("length")
        self.assertTrue(self.unlocked("cut_short"))
        self.assertFalse(self.unlocked("token_wall"))

    def test_token_wall_at_25(self):
        for _ in range(25):
            self._fire("length")
        self.assertTrue(self.unlocked("cut_short"))
        self.assertTrue(self.unlocked("token_wall"))

    def test_count_persists_across_mixed(self):
        for _ in range(3):
            self._fire("length")
        for _ in range(5):
            self._fire("stop")
        self.assertEqual(self.stats()["truncated_responses"], 3)

    def test_missing_finish_reason_noop(self):
        self.mod._post_api_request(
            usage={"total_tokens": 1000}, api_duration=1.0,
            model="m1", provider="p1", api_call_count=1, message_count=10,
        )
        self.assertEqual(self.stats().get("truncated_responses", 0), 0)


class TestSubagentRuntime(HookTestBase):
    """subagent_stop duration_ms: how long delegated children ran."""

    def _stop(self, duration_ms=500):
        self.mod._on_subagent_stop(
            child_role="leaf", child_status="completed",
            duration_ms=duration_ms,
        )

    def test_fast_subagent_no_unlock(self):
        self._stop(500)
        self.assertFalse(self.unlocked("slow_thinker"))
        self.assertFalse(self.unlocked("marathon"))
        self.assertEqual(self.stats().get("longest_subagent_ms", 0), 500)

    def test_slow_thinker_at_10_minutes(self):
        self._stop(10 * 60 * 1000)
        self.assertTrue(self.unlocked("slow_thinker"))
        self.assertFalse(self.unlocked("marathon"))

    def test_marathon_at_60_minutes(self):
        self._stop(60 * 60 * 1000)
        self.assertTrue(self.unlocked("slow_thinker"))
        self.assertTrue(self.unlocked("marathon"))

    def test_peak_keeps_max(self):
        self._stop(500)
        self._stop(30 * 60 * 1000)
        self.assertEqual(self.stats()["longest_subagent_ms"], 30 * 60 * 1000)

    def test_missing_duration_noop(self):
        self.mod._on_subagent_stop(child_role="leaf", child_status="completed")
        self.assertEqual(self.stats().get("longest_subagent_ms", 0), 0)


class TestToolInterrupts(HookTestBase):
    """post_tool_call status=\"cancelled\": user pressed stop mid-tool."""

    def _interrupt(self):
        self.mod._post_tool_call(
            tool_name="terminal", args={}, session_id="s-int",
            duration_ms=200, status="cancelled",
            error_type="keyboard_interrupt",
        )

    def test_ok_status_does_not_count_interrupt(self):
        self.mod._post_tool_call(
            tool_name="terminal", args={}, session_id="s-int",
            duration_ms=200, status="ok",
        )
        self.assertEqual(self.stats().get("tool_interrupts", 0), 0)
        self.assertFalse(self.unlocked("manual_override"))

    def test_first_interrupt_unlocks_manual_override(self):
        self._interrupt()
        self.assertTrue(self.unlocked("manual_override"))
        self.assertFalse(self.unlocked("backseat_driver"))
        self.assertEqual(self.stats()["tool_interrupts"], 1)

    def test_five_interrupts_unlock_backseat_driver(self):
        for _ in range(5):
            self._interrupt()
        self.assertTrue(self.unlocked("manual_override"))
        self.assertTrue(self.unlocked("backseat_driver"))
        self.assertFalse(self.unlocked("control_freak"))

    def test_fifteen_interrupts_unlock_control_freak(self):
        for _ in range(15):
            self._interrupt()
        self.assertTrue(self.unlocked("control_freak"))
        self.assertEqual(self.stats()["tool_interrupts"], 15)


class TestToolBlocks(HookTestBase):
    """post_tool_call status=\"blocked\": policy denied the tool pre-run."""

    def _block(self):
        self.mod._post_tool_call(
            tool_name="write_file", args={}, session_id="s-blk",
            duration_ms=0, status="blocked",
            error_type="guardrail_block",
        )

    def test_error_status_does_not_count_block(self):
        self.mod._post_tool_call(
            tool_name="write_file", args={}, session_id="s-blk",
            duration_ms=0, status="error", error_type="boom",
        )
        self.assertEqual(self.stats().get("tool_blocks", 0), 0)
        self.assertFalse(self.unlocked("dead_end"))

    def test_first_block_unlocks_dead_end(self):
        self._block()
        self.assertTrue(self.unlocked("dead_end"))
        self.assertFalse(self.unlocked("brick_wall"))
        self.assertEqual(self.stats()["tool_blocks"], 1)

    def test_ten_blocks_unlock_brick_wall(self):
        for _ in range(10):
            self._block()
        self.assertTrue(self.unlocked("dead_end"))
        self.assertTrue(self.unlocked("brick_wall"))
        self.assertEqual(self.stats()["tool_blocks"], 10)


class TestCoverageEdges(HookTestBase):
    """Close the remaining uncovered branches (coverage hardening).

    Each test targets a specific defensive / normalization path that the
    main suites never reach: list→set state migration, base_url edge
    cases, message_type-only media detection, badge rendering variants,
    formatter boundaries, the lock double-check, and the empty-group
    summary skip. Keeping 100% line coverage means a future refactor that
    breaks one of these paths fails CI instead of silently rotting.
    """

    def test_load_state_inner_double_check(self):
        # Line 223: the second `if _state is not None` inside the lock.
        # Deterministic: main holds the lock the whole time, so the worker
        # MUST pass the outer None-check and block on the lock before main
        # sets _state — after release the worker returns via the inner check.
        import threading
        import time
        mod = self.mod
        mod._state = None
        results = {}

        def worker():
            results["state"] = mod._load_state()

        with mod._state_lock:
            t = threading.Thread(target=worker)
            t.start()
            time.sleep(0.3)  # worker passed outer check, blocked on lock
            mod._state = {"achievements": {}, "stats": {}}
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertIsNotNone(results.get("state"))

    def test_env_types_list_normalized_on_load(self):
        # Older persisted state stored sets as lists — the transform
        # handler must convert before adding (line 1973-1974).
        st = self.stats()
        st["env_types"] = ["local"]  # as loaded from JSON
        self.mod._transform_terminal_output(
            output="x", env_type="docker", returncode=0,
        )
        envs = self.stats()["env_types"]
        self.assertIsInstance(envs, set)
        self.assertIn("docker", envs)
        self.assertIn("local", envs)
        self.assertTrue(self.unlocked("multi_env"))  # 2 envs

    def test_is_local_base_url_empty_or_non_string(self):
        self.assertFalse(self.mod._is_local_base_url(None))
        self.assertFalse(self.mod._is_local_base_url(""))
        self.assertFalse(self.mod._is_local_base_url("   "))
        self.assertFalse(self.mod._is_local_base_url(12345))

    def test_is_local_base_url_malformed_url(self):
        # urlparse raises ValueError on e.g. an unclosed IPv6 bracket;
        # the handler must degrade to host="" → False (line 2285-2286).
        self.assertFalse(self.mod._is_local_base_url("http://[::1"))

    def test_message_type_only_media_counts(self):
        # A gateway event may signal media via message_type alone (e.g.
        # "voice") with empty media_urls — line 2662.
        class _Event:
            pass

        class _Source:
            pass

        src = _Source()
        src.platform = "discord"
        src.user_id = "user-media-type"
        src.user_name = "mt"
        src.is_bot = False
        ev = _Event()
        ev.internal = False
        ev.source = src
        ev.media_urls = []
        ev.media_types = []
        ev.message_type = "voice"
        self.mod._on_pre_gateway_dispatch(
            event=ev, gateway=None, session_store=None,
        )
        self.assertEqual(self.stats()["media_messages"], 1)
        self.assertTrue(self.unlocked("show_and_tell"))

    def test_format_badge_non_compact_with_progress(self):
        # The non-compact badge renders a progress bar + detail_progress
        # template (line 2721-2722) — only reachable via direct call since
        # the group view uses compact=True.
        mod = self.mod
        state = mod._load_state()
        a_def = mod.ACHIEVEMENT_DEFS["social_butterfly"]
        state["achievements"]["social_butterfly"] = {
            "unlocked": False,
            "progress": {"current": 2, "target": 3},
        }
        out = mod._format_badge(
            "social_butterfly", a_def, state, compact=False,
        )
        self.assertIn("⬜", out)  # locked badge icon
        self.assertIn("Social Butterfly", out)
        self.assertIn("2/3", out)
        self.assertIn("Progress", out)

    def test_format_bytes_small_branch(self):
        self.assertEqual(self.mod._format_bytes(0), "0 B")
        self.assertEqual(self.mod._format_bytes(500), "500 B")
        self.assertEqual(self.mod._format_bytes(1023), "1023 B")
        self.assertEqual(self.mod._format_bytes(1024), "1.0 KiB")

    def test_format_duration_hours_branch(self):
        self.assertEqual(self.mod._format_duration(0), "0s")
        self.assertEqual(self.mod._format_duration(2 * 3600 * 1000 + 5 * 60 * 1000), "2h 5m")
        self.assertEqual(self.mod._format_duration(12 * 60 * 1000 + 30 * 1000), "12m 30s")

    def test_summary_skips_empty_group(self):
        # GROUPS is static and every group has defs, but the summary loop
        # guards against an empty group (line 3006) — prove the guard.
        mod = self.mod
        original = mod.GROUPS
        try:
            mod.GROUPS = list(original) + ["Phantom Group"]
            out = mod._handle_achievements("")
        finally:
            mod.GROUPS = original
        self.assertNotIn("Phantom Group", out)


class TestApprovalRequest(HookTestBase):
    """pre_approval_request: approval gates drive Under Scrutiny."""

    def test_ten_requests_unlock_under_scrutiny(self):
        for _ in range(10):
            self.mod._on_approval_request(
                command="rm -rf /tmp/x", description="dangerous",
                pattern_key="rm_rf", session_key="s", surface="gateway",
            )
        self.assertTrue(self.unlocked("under_scrutiny"))
        self.assertEqual(self.stats()["approval_requests"], 10)

    def test_progress_before_threshold(self):
        for _ in range(4):
            self.mod._on_approval_request(
                command="cmd", description="d", pattern_key="k",
                session_key="s", surface="cli",
            )
        self.assertFalse(self.unlocked("under_scrutiny"))
        st = self.mod._load_state()["achievements"]["under_scrutiny"]
        self.assertEqual(st["progress"]["current"], 4)
        self.assertEqual(st["progress"]["target"], 10)

    def test_single_request_counts(self):
        self.mod._on_approval_request(
            command="cmd", description="d", pattern_key="k",
            session_key="s", surface="gateway",
        )
        self.assertEqual(self.stats()["approval_requests"], 1)
        self.assertFalse(self.unlocked("under_scrutiny"))

    def test_approval_requests_and_responses_are_independent(self):
        # Prompts raised but never answered still count toward scrutiny
        for _ in range(10):
            self.mod._on_approval_request(
                command="cmd", description="d", pattern_key="k",
                session_key="s", surface="gateway",
            )
        self.assertTrue(self.unlocked("under_scrutiny"))
        # No response hooks fired — no trust/caution/yolo
        self.assertFalse(self.unlocked("trust_fall"))
        self.assertFalse(self.unlocked("cautious"))
        self.assertFalse(self.unlocked("yolo_mode"))


class TestPreGatewayDispatch(HookTestBase):
    """pre_gateway_dispatch: distinct senders drive Social Butterfly."""

    def _event(self, platform, user_id, user_name=None, is_bot=False, internal=False,
               media=False, text=""):
        class _Source:
            pass

        class _Event:
            def __init__(self, txt):
                self._text = txt

            def is_command(self):
                return bool(self._text.startswith("/"))

            def get_command(self):
                if not self.is_command():
                    return None
                parts = self._text.split(maxsplit=1)
                raw = parts[0][1:].lower() if parts else None
                if raw and "/" in raw:
                    return None
                return raw

        src = _Source()
        src.platform = platform
        src.user_id = user_id
        src.user_name = user_name
        src.is_bot = is_bot
        ev = _Event(text)
        ev.internal = internal
        ev.source = src
        ev.media_urls = ["/tmp/pic.jpg"] if media else []
        ev.media_types = ["image/jpeg"] if media else []
        ev.message_type = "photo" if media else "text"
        return ev

    def test_three_users_unlock_social_butterfly(self):
        for uid in ("user-a", "user-b", "user-c"):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", uid), gateway=None, session_store=None,
            )
        self.assertTrue(self.unlocked("social_butterfly"))
        self.assertFalse(self.unlocked("party_host"))
        self.assertEqual(len(self.stats()["users_seen"]), 3)

    def test_ten_users_unlock_party_host(self):
        for i in range(10):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", f"user-{i}"),
                gateway=None, session_store=None,
            )
        self.assertTrue(self.unlocked("social_butterfly"))
        self.assertTrue(self.unlocked("party_host"))

    def test_progress_before_threshold(self):
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "user-a"), gateway=None, session_store=None,
        )
        self.assertFalse(self.unlocked("social_butterfly"))
        st = self.mod._load_state()["achievements"]["social_butterfly"]
        self.assertEqual(st["progress"]["current"], 1)
        self.assertEqual(st["progress"]["target"], 3)

    def test_same_user_repeats_do_not_inflate_count(self):
        for _ in range(5):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a"),
                gateway=None, session_store=None,
            )
        self.assertEqual(len(self.stats()["users_seen"]), 1)
        self.assertFalse(self.unlocked("social_butterfly"))

    def test_platform_scoped_identity(self):
        # Same user_id on different platforms counts as two identities
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "42"), gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._event("telegram", "42"), gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats()["users_seen"]), 2)

    def test_bots_and_internal_events_ignored(self):
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "webhook", is_bot=True),
            gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "sys", internal=True),
            gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats().get("users_seen", set())), 0)

    def test_no_event_is_noop(self):
        self.mod._on_pre_gateway_dispatch(gateway=None, session_store=None)
        self.assertEqual(len(self.stats().get("users_seen", set())), 0)

    def test_no_user_identity_is_noop(self):
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", None, user_name=None),
            gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats().get("users_seen", set())), 0)

    # ── Media messages (Show and Tell / Visual Storyteller) ──

    def test_first_media_message_unlocks_show_and_tell(self):
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "user-a", media=True),
            gateway=None, session_store=None,
        )
        self.assertTrue(self.unlocked("show_and_tell"))
        self.assertFalse(self.unlocked("visual_storyteller"))
        self.assertEqual(self.stats()["media_messages"], 1)

    def test_twenty_five_media_messages_unlock_visual_storyteller(self):
        for _ in range(25):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", media=True),
                gateway=None, session_store=None,
            )
        self.assertTrue(self.unlocked("show_and_tell"))
        self.assertTrue(self.unlocked("visual_storyteller"))
        self.assertEqual(self.stats()["media_messages"], 25)

    def test_media_progress_before_threshold(self):
        for _ in range(5):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", media=True),
                gateway=None, session_store=None,
            )
        self.assertFalse(self.unlocked("visual_storyteller"))
        st = self.mod._load_state()["achievements"]["visual_storyteller"]
        self.assertEqual(st["progress"]["current"], 5)
        self.assertEqual(st["progress"]["target"], 25)

    def test_text_messages_do_not_count_as_media(self):
        for _ in range(10):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a"),
                gateway=None, session_store=None,
            )
        self.assertFalse(self.unlocked("show_and_tell"))
        self.assertNotIn("media_messages", self.stats())

    def test_media_urls_alone_counts_without_message_type(self):
        # A gateway event may carry media_urls while message_type stays
        # "text" (e.g. inline images) — media_urls must still trigger it.
        ev = self._event("discord", "user-a")
        ev.media_urls = ["/tmp/vid.mp4"]
        ev.media_types = []
        ev.message_type = "text"
        self.mod._on_pre_gateway_dispatch(
            event=ev, gateway=None, session_store=None,
        )
        self.assertTrue(self.unlocked("show_and_tell"))


class TestGatewayCommands(HookTestBase):
    """pre_gateway_dispatch command detection: slash commands the gateway
    intercepts BEFORE the LLM (/new, /reset, /achievements) never reach
    post_llm_call — they're only observable here. Attribution matters:
    this hook fires for ALL users pre-auth, so only the platform's PRIMARY
    user (first non-bot seen = the owner) counts toward achievements."""

    def _event(self, platform, user_id, user_name=None, is_bot=False, internal=False,
               text=""):
        class _Source:
            pass

        class _Event:
            def __init__(self, txt):
                self._text = txt

            @property
            def text(self):
                return self._text

            def is_command(self):
                return bool(self._text.startswith("/"))

            def get_command(self):
                if not self.is_command():
                    return None
                parts = self._text.split(maxsplit=1)
                raw = parts[0][1:].lower() if parts else None
                if raw and "/" in raw:
                    return None
                return raw

        src = _Source()
        src.platform = platform
        src.user_id = user_id
        src.user_name = user_name
        src.is_bot = is_bot
        ev = _Event(text)
        ev.internal = internal
        ev.source = src
        ev.media_urls = []
        ev.media_types = []
        ev.message_type = "text"
        return ev

    def test_three_gateway_commands_unlock_slash_commander(self):
        # /achievements never reaches the LLM — the gateway path must count it
        for cmd in ("/new", "/reset", "/achievements"):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", text=cmd),
                gateway=None, session_store=None,
            )
        self.assertTrue(self.unlocked("slash_commander"))
        self.assertEqual(len(self.stats()["slash_commands_used"]), 3)

    def test_ten_commands_unlock_command_center(self):
        cmds = [f"/cmd{i}" for i in range(10)]
        for cmd in cmds:
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", text=cmd),
                gateway=None, session_store=None,
            )
        self.assertTrue(self.unlocked("command_center"))
        self.assertFalse(self.unlocked("command_general"))

    def test_twenty_five_commands_unlock_command_general(self):
        for i in range(25):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", text=f"/cmd{i}"),
                gateway=None, session_store=None,
            )
        self.assertTrue(self.unlocked("command_center"))
        self.assertTrue(self.unlocked("command_general"))

    def test_repeated_same_command_counts_once(self):
        for _ in range(10):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", text="/new"),
                gateway=None, session_store=None,
            )
        self.assertFalse(self.unlocked("slash_commander"))
        self.assertEqual(len(self.stats()["slash_commands_used"]), 1)

    def test_other_users_commands_do_not_count(self):
        # First user seen is PRIMARY — a second user's commands must not
        # count toward the owner's achievements.
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "owner", text="/new"),
            gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "stranger", text="/reset"),
            gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "stranger", text="/title"),
            gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats()["slash_commands_used"]), 1)
        self.assertFalse(self.unlocked("slash_commander"))

    def test_primary_user_per_platform_is_independent(self):
        # Each platform has its own primary user
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "owner", text="/new"),
            gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._event("telegram", "tg-owner", text="/reset"),
            gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._event("telegram", "tg-owner", text="/title"),
            gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats()["slash_commands_used"]), 3)

    def test_plain_text_has_no_command(self):
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "user-a", text="hello there"),
            gateway=None, session_store=None,
        )
        # slash_commands_used is a default key — assert it stays EMPTY
        self.assertEqual(len(self.stats().get("slash_commands_used", set())), 0)
        self.assertFalse(self.unlocked("slash_commander"))

    def test_no_get_command_method_falls_back_to_text(self):
        # Older/synthetic events may lack get_command — text parsing covers it
        ev = self._event("discord", "user-a", text="/new")
        delattr(type(ev), "get_command")
        self.mod._on_pre_gateway_dispatch(
            event=ev, gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats()["slash_commands_used"]), 1)

    def test_raising_get_command_falls_back_to_text(self):
        # A broken get_command must not crash the hook — text parsing covers it
        ev = self._event("discord", "user-a", text="/reset")

        def _boom(self):
            raise RuntimeError("adapter bug")

        ev.get_command = _boom.__get__(ev)
        self.mod._on_pre_gateway_dispatch(
            event=ev, gateway=None, session_store=None,
        )
        self.assertEqual(len(self.stats()["slash_commands_used"]), 1)

    def test_llm_and_gateway_paths_dedupe(self):
        # Same command via post_llm_call (/title with slash) and gateway
        # (title without) must count ONCE — canonical form strips the slash
        self.mod._on_pre_gateway_dispatch(
            event=self._event("discord", "user-a", text="/title"),
            gateway=None, session_store=None,
        )
        self.mod._post_llm_call(
            user_message="/title my session", conversation_history=[
                {"role": "user", "content": "/title my session"},
            ],
            model="m1", platform="discord",
        )
        self.assertEqual(len(self.stats()["slash_commands_used"]), 1)

    def test_command_center_progress_before_threshold(self):
        for i in range(5):
            self.mod._on_pre_gateway_dispatch(
                event=self._event("discord", "user-a", text=f"/cmd{i}"),
                gateway=None, session_store=None,
            )
        self.assertFalse(self.unlocked("command_center"))
        st = self.mod._load_state()["achievements"]["command_center"]
        self.assertEqual(st["progress"]["current"], 5)
        self.assertEqual(st["progress"]["target"], 10)


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

    def test_post_llm_call_empty_user_message(self):
        # post_llm_call must tolerate missing/empty user_message
        self.mod._post_llm_call(
            user_message="", conversation_history=[], model="m1", platform="cli")
        self.assertFalse(self.unlocked("first_steps"))

    def test_multi_model_at_5_models(self):
        for i in range(5):
            self.turn(f"hi {i}", model=f"model-{i}", platform="discord")
        self.assertTrue(self.unlocked("multi_model"))
        self.assertFalse(self.unlocked("model_collector"))

    def test_model_collector_at_10_models(self):
        for i in range(10):
            self.turn(f"hi {i}", model=f"model-{i}", platform="discord")
        self.assertTrue(self.unlocked("model_collector"))

    def test_cross_platform_veteran_at_5_platforms(self):
        for i, plat in enumerate(["discord", "telegram", "whatsapp", "slack", "matrix"]):
            self.turn(f"hi {i}", model=f"m-{i}", platform=plat)
        self.assertTrue(self.unlocked("cross_platform_veteran"))
        self.assertTrue(self.unlocked("gateway_networker"))

    def test_gateway_networker_progress_before_3(self):
        self.turn("hi", model="m1", platform="discord")
        self.turn("hi2", model="m1", platform="telegram")
        self.assertFalse(self.unlocked("gateway_networker"))
        st = self.mod._load_state()["achievements"]["gateway_networker"]
        self.assertEqual(st["progress"]["current"], 2)

    def test_platform_falls_back_to_cli_when_missing(self):
        self.mod._post_llm_call(
            user_message="hi", conversation_history=[{"role": "user", "content": "hi"}],
            model="m1", platform="",
        )
        self.assertIn("cli", self.stats()["platforms"])

    def test_post_tool_call_without_tool_name_is_noop(self):
        self.mod._post_tool_call(args={}, session_id="s", duration_ms=10)
        st = self.stats()
        self.assertEqual(st.get("total_tool_calls", 0), 0)

    # ── Message verbosity (Wordsmith / Novelist) ──

    def test_wordsmith_at_300_words(self):
        self.turn("word " * 300)
        self.assertTrue(self.unlocked("wordsmith"))
        self.assertFalse(self.unlocked("novelist"))
        self.assertEqual(self.stats()["longest_message_words"], 300)

    def test_novelist_at_1500_words(self):
        self.turn("word " * 1500)
        self.assertTrue(self.unlocked("wordsmith"))
        self.assertTrue(self.unlocked("novelist"))
        self.assertEqual(self.stats()["longest_message_words"], 1500)

    def test_wordsmith_progress_before_threshold(self):
        self.turn("word " * 100)
        self.assertFalse(self.unlocked("wordsmith"))
        st = self.mod._load_state()["achievements"]["wordsmith"]
        self.assertEqual(st["progress"]["current"], 100)
        self.assertEqual(st["progress"]["target"], 300)

    def test_longest_message_keeps_max_not_last(self):
        self.turn("word " * 100)
        self.turn("word " * 450)
        self.turn("word " * 200)
        self.assertEqual(self.stats()["longest_message_words"], 450)
        self.assertTrue(self.unlocked("wordsmith"))

    def test_empty_message_no_verbosity_stat(self):
        self.mod._post_llm_call(
            user_message="", conversation_history=[], model="m1", platform="cli")
        self.assertNotIn("longest_message_words", self.stats())

    def test_tool_call_with_non_dict_args(self):
        # args not a dict must not crash _check_tool_args
        self.mod._post_tool_call(tool_name="cronjob", args="bad", session_id="s")
        self.assertFalse(self.unlocked("cron_commander"))


class TestStreakEdgeCases(HookTestBase):
    """on_session_end streak bookkeeping edge cases."""

    def test_streak_increments_consecutive_days(self):
        from datetime import date, timedelta
        self.mod._on_session_end(session_id="s1")
        st = self.stats()
        self.assertEqual(st["current_streak"], 1)
        # Backdate last_active to yesterday, then fire end again → 2
        st["last_active_date"] = (date.today() - timedelta(days=1)).isoformat()  # noqa: DTZ011
        self.mod._on_session_end(session_id="s2")
        self.assertEqual(self.stats()["current_streak"], 2)

    def test_streak_resets_after_gap(self):
        from datetime import date, timedelta
        self.mod._on_session_end(session_id="s1")
        st = self.stats()
        st["last_active_date"] = (date.today() - timedelta(days=3)).isoformat()  # noqa: DTZ011
        self.mod._on_session_end(session_id="s2")
        self.assertEqual(self.stats()["current_streak"], 1)

    def test_streak_survives_invalid_date(self):
        self.mod._on_session_end(session_id="s1")
        st = self.stats()
        st["last_active_date"] = "not-a-date"
        self.mod._on_session_end(session_id="s2")
        self.assertEqual(self.stats()["current_streak"], 1)

    def test_streak_same_day_does_not_double(self):
        self.mod._on_session_end(session_id="s1")
        self.mod._on_session_end(session_id="s2")
        self.assertEqual(self.stats()["current_streak"], 1)

    def test_streak_future_date_resets(self):
        # last_active in the future (clock skew) → reset, don't crash
        from datetime import date, timedelta
        self.mod._on_session_end(session_id="s1")
        st = self.stats()
        st["last_active_date"] = (date.today() + timedelta(days=1)).isoformat()  # noqa: DTZ011
        self.mod._on_session_end(session_id="s2")
        self.assertEqual(self.stats()["current_streak"], 1)

    def test_locale_load_ignores_unreadable_dir(self):
        # Unreadable locale dir → empty cache with en fallback, no crash
        old_dir = self.mod._LOCALES_DIR
        self.mod._LOCALES_DIR = "/proc/definitely/not/a/locales/dir"
        self.mod._locales_cache = {}
        try:
            cache = self.mod._load_locales()
            self.assertIn("en", cache)
        finally:
            self.mod._LOCALES_DIR = old_dir
            self.mod._locales_cache = {}

    def test_locale_load_falls_back_to_wheel_data_dir(self):
        # When the HERMES_HOME locales dir is missing, the wheel-shipped
        # data dir is used — pip-installed copy works.
        old_dir = self.mod._LOCALES_DIR
        old_wheel = self.mod._WHEEL_DATA_DIR
        self.mod._LOCALES_DIR = "/proc/definitely/not/a/locales/dir"
        # Simulate a pip-installed copy: wheel data dir = checkout locales
        self.mod._WHEEL_DATA_DIR = LOCALES_DIR
        self.mod._locales_cache = {}
        try:
            cache = self.mod._load_locales()
            self.assertEqual(len(cache.get("en", {}).get("achievement", {})), 153)
        finally:
            self.mod._LOCALES_DIR = old_dir
            self.mod._WHEEL_DATA_DIR = old_wheel
            self.mod._locales_cache = {}

    def test_wheel_data_dir_default_points_at_sys_prefix(self):
        # Regression (v2.18.5): _WHEEL_DATA_DIR used to guess
        # "<site-packages>/achievements/locales", which NEVER exists —
        # setuptools data-files are prefix-relative and flattened, so a
        # pip-installed wheel silently lost its locales (English-only
        # fallback). The default must derive from sys.prefix.
        import sys as _sys
        self.assertEqual(self.mod._WHEEL_DATA_DIR,
                         os.path.join(_sys.prefix, "achievements"))


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

    def test_notifications_batch_coalesce_in_debounce_window(self):
        # Rapid unlocks → one batched message with N embeds
        captured = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured.append(req.data)
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "456",
        }.get(key, fallback)
        # Drop the gateway origin env so only the home channel is a target
        old_origin = os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            # Fire 3 unlocks within the debounce window
            self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS["first_steps"])
            self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS["chatty"])
            self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS["night_owl"])
            # Simulate the debounce timer firing
            self.mod._flush_notification_queue()
        finally:
            self.mod.urllib.request.urlopen = old
            if old_origin is not None:
                os.environ["HERMES_SESSION_CHAT_ID"] = old_origin

        # Exactly ONE message with 3 embeds (not 3 messages)
        self.assertEqual(len(captured), 1)
        import json as _json
        payload = _json.loads(captured[0])
        self.assertEqual(len(payload["embeds"]), 3)
        self.assertIn("3 achievements unlocked", payload["content"])

    def test_flush_with_empty_queue_is_noop(self):
        # No pending notifications → flush does nothing, no HTTP
        old = self.mod.urllib.request.urlopen
        calls = []
        self.mod.urllib.request.urlopen = lambda *a, **k: calls.append(a)
        try:
            self.mod._flush_notification_queue()
        finally:
            self.mod.urllib.request.urlopen = old
        self.assertEqual(calls, [])

    def test_batch_skips_without_token(self):
        # No token → silent no-op even with queued items
        self.mod._load_env_var = lambda key, fallback="": ""
        old = self.mod.urllib.request.urlopen
        calls = []
        self.mod.urllib.request.urlopen = lambda *a, **k: calls.append(a)
        try:
            self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS["first_steps"])
            self.mod._flush_notification_queue()
        finally:
            self.mod.urllib.request.urlopen = old
        self.assertEqual(calls, [])

    def test_batch_chunks_over_10_embeds(self):
        # Discord caps embeds at 10/message — 12 unlocks → 2 messages
        captured = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured.append(req.data)
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "456",
        }.get(key, fallback)
        old_origin = os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            ids = list(self.mod.ACHIEVEMENT_DEFS.keys())[:12]
            for aid in ids:
                self.mod._send_discord_notification(self.mod.ACHIEVEMENT_DEFS[aid])
            self.mod._flush_notification_queue()
        finally:
            self.mod.urllib.request.urlopen = old
            if old_origin is not None:
                os.environ["HERMES_SESSION_CHAT_ID"] = old_origin

        import json as _json
        self.assertEqual(len(captured), 2)  # 10 + 2
        first = _json.loads(captured[0])
        second = _json.loads(captured[1])
        self.assertEqual(len(first["embeds"]), 10)
        self.assertEqual(len(second["embeds"]), 2)
        # Only the first chunk carries the batch header
        self.assertIn("achievements unlocked", first["content"])
        self.assertEqual(second["content"], "")

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

    def test_corrupted_primary_and_backup_reset_to_fresh(self):
        self.tool_call("terminal", {}, session_id="sess-double")
        self.mod._save_state(force=True)
        # Corrupt BOTH the primary state and the backup — must reset cleanly
        for path in (self.mod._STATE_PATH, self.mod._STATE_BAK_PATH):
            with open(path, "w") as f:
                f.write("{also corrupted!!!")
        self.mod._state = None
        st = self.mod._load_state()
        # Fresh state: all achievements exist but none unlocked, stats zeroed
        self.assertEqual(st["stats"]["total_turns"], 0)
        self.assertEqual(len(st["achievements"]), len(self.mod.ACHIEVEMENT_DEFS))
        self.assertFalse(any(a.get("unlocked") for a in st["achievements"].values()))

    def test_corrupted_scalar_stats_normalized(self):
        # A corrupted/legacy state with scalar stats must not crash hooks
        state = self.mod._load_state()
        state["stats"]["platforms"] = 0  # scalar instead of set
        state["stats"]["models_used"] = None
        state["stats"]["slash_commands_used"] = "oops"
        state["stats"]["active_session"] = {"id": None, "calls": 0,
                                            "tool_names": "not-a-set", "fast_streak": 0}
        # Persist the corruption so a reload actually hits _normalize_state
        self.mod._save_state(force=True)
        self.mod._state = None
        st = self.mod._load_state()["stats"]
        self.assertEqual(st["platforms"], set())
        self.assertEqual(st["models_used"], set())
        self.assertEqual(st["slash_commands_used"], set())
        self.assertEqual(st["active_session"]["tool_names"], set())

    def test_corrupted_active_session_non_dict_normalized(self):
        # active_session persisted as a non-dict → replaced with fresh shape
        state = self.mod._load_state()
        state["stats"]["active_session"] = "garbage"
        self.mod._save_state(force=True)
        self.mod._state = None
        st = self.mod._load_state()["stats"]["active_session"]
        self.assertEqual(st["id"], None)
        self.assertEqual(st["calls"], 0)
        self.assertEqual(st["tool_names"], set())
        self.assertEqual(st["fast_streak"], 0)

    def test_active_session_missing_scalar_keys_defaulted(self):
        # A dict active_session missing id/calls/fast_streak gets defaults
        state = self.mod._load_state()
        state["stats"]["active_session"] = {"tool_names": ["terminal"]}
        self.mod._save_state(force=True)
        self.mod._state = None
        st = self.mod._load_state()["stats"]["active_session"]
        self.assertEqual(st["id"], None)
        self.assertEqual(st["calls"], 0)
        self.assertEqual(st["fast_streak"], 0)
        self.assertEqual(st["tool_names"], {"terminal"})

    def test_stale_achievement_entries_pruned(self):
        # Entries for removed/renamed achievements must not linger in state
        state = self.mod._load_state()
        state["achievements"]["star_gazer"] = {"unlocked": True}  # removed def
        state["achievements"]["made_up_old_id"] = {"unlocked": True}
        self.mod._save_state(force=True)
        self.mod._state = None
        st = self.mod._load_state()["achievements"]
        self.assertNotIn("star_gazer", st)
        self.assertNotIn("made_up_old_id", st)
        self.assertEqual(len(st), len(self.mod.ACHIEVEMENT_DEFS))

    def test_state_save_failure_is_swallowed(self):
        # If the state dir can't be written, _save_state must not raise
        self.tool_call("terminal", {}, session_id="sess-savefail")
        # Point state path at an impossible location
        old_path, old_bak = self.mod._STATE_PATH, self.mod._STATE_BAK_PATH
        self.mod._STATE_PATH = "/proc/definitely/not/writable/state.json"
        self.mod._STATE_BAK_PATH = "/proc/definitely/not/writable/state.json.bak"
        try:
            self.mod._save_state(force=True)  # must not raise
        finally:
            self.mod._STATE_PATH, self.mod._STATE_BAK_PATH = old_path, old_bak

    def test_backup_copy_failure_is_swallowed(self):
        # Backup copy failing (OSError) must not abort the state save
        self.tool_call("terminal", {}, session_id="sess-bakfail")
        self.mod._save_state(force=True)  # primary written, backup created
        # Point the backup at an unwritable path so copy2 raises OSError
        old_bak = self.mod._STATE_BAK_PATH
        self.mod._STATE_BAK_PATH = "/proc/definitely/not/writable/state.json.bak"
        try:
            self.mod._save_state(force=True)  # must not raise
        finally:
            self.mod._STATE_BAK_PATH = old_bak

    def test_load_env_var_reads_dotenv_and_env(self):
        # .env file wins over os.environ
        os.environ["ACH_TEST_VAR"] = "from-env"
        env_file = os.path.join(self._tmp, ".env")
        with open(env_file, "w") as f:
            f.write("# comment\n\nACH_TEST_VAR=from-dotenv\n")
        try:
            self.assertEqual(self.mod._load_env_var("ACH_TEST_VAR"), "from-dotenv")
            self.assertEqual(self.mod._load_env_var("MISSING_VAR"), "")
            self.assertEqual(self.mod._load_env_var("MISSING_VAR", "fallback"), "fallback")
        finally:
            os.environ.pop("ACH_TEST_VAR", None)

    def test_notification_worker_never_crashes(self):
        # A broken notification must be caught in the debounce flush
        ach_def = self.mod.ACHIEVEMENT_DEFS["first_steps"]

        def boom(batch):
            raise RuntimeError("discord is down")

        old = self.mod._send_discord_notification_batch
        self.mod._send_discord_notification_batch = boom
        try:
            self.mod._send_discord_notification(ach_def)  # must not raise
            self.mod._flush_notification_queue()  # must swallow the boom
        finally:
            self.mod._send_discord_notification_batch = old

    def test_load_env_var_ignores_unreadable_dotenv(self):
        # Unreadable .env must fall back to os.environ, not raise
        env_file = os.path.join(self._tmp, ".env")
        with open(env_file, "w") as f:
            f.write("ACH_TEST_VAR=from-dotenv\n")
        # Point _load_env_var at a non-existent path by removing the file
        os.remove(env_file)
        os.environ["ACH_TEST_VAR"] = "from-env"
        try:
            self.assertEqual(self.mod._load_env_var("ACH_TEST_VAR"), "from-env")
        finally:
            os.environ.pop("ACH_TEST_VAR", None)

    def test_load_env_var_skips_malformed_lines(self):
        # Lines without '=' are skipped; valid lines still parsed
        env_file = os.path.join(self._tmp, ".env")
        with open(env_file, "w") as f:
            f.write("NO_EQUALS_HERE\nACH_TEST_VAR=from-dotenv\n")
        try:
            self.assertEqual(self.mod._load_env_var("ACH_TEST_VAR"), "from-dotenv")
        finally:
            os.environ.pop("ACH_TEST_VAR", None)

    def test_send_notification_sync_skips_without_token(self):
        # No token configured → silent no-op, no HTTP attempt
        self.mod._load_env_var = lambda key, fallback="": ""
        old = self.mod.urllib.request.urlopen
        calls = []
        self.mod.urllib.request.urlopen = lambda *a, **k: calls.append(a)
        try:
            self.mod._send_discord_notification_sync(
                self.mod.ACHIEVEMENT_DEFS["first_steps"])
        finally:
            self.mod.urllib.request.urlopen = old
        self.assertEqual(calls, [])

    def test_send_notification_sync_swallows_network_error(self):
        # A URLError from Discord must be logged, not raised
        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "123",
        }.get(key, fallback)

        def boom(req, timeout=None):
            raise self.mod.urllib.error.URLError("network down")

        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = boom
        try:
            self.mod._send_discord_notification_sync(
                self.mod.ACHIEVEMENT_DEFS["first_steps"])  # must not raise
        finally:
            self.mod.urllib.request.urlopen = old

    def test_send_notification_sync_swallows_http_error(self):
        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "123",
        }.get(key, fallback)

        def boom(req, timeout=None):
            raise self.mod.urllib.error.HTTPError(
                "https://discord.com/api/v10/channels/123/messages",
                429, "rate limited", None, None)

        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = boom
        try:
            self.mod._send_discord_notification_sync(
                self.mod.ACHIEVEMENT_DEFS["first_steps"])  # must not raise
        finally:
            self.mod.urllib.request.urlopen = old

    def test_send_notification_sync_sends_once_when_home_equals_origin(self):
        # home == origin → only one POST (dedup)
        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "456",
        }.get(key, fallback)
        os.environ["HERMES_SESSION_CHAT_ID"] = "456"  # same as home
        calls = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            return FakeResp()

        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            self.mod._send_discord_notification_sync(
                self.mod.ACHIEVEMENT_DEFS["first_steps"])
        finally:
            self.mod.urllib.request.urlopen = old
            os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        self.assertEqual(len(calls), 1)

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

    def test_origin_skipped_when_not_discord_snowflake(self):
        # Non-Discord origin (WhatsApp/Telegram chat ID) must not POST to
        # the Discord API with a bogus channel ID — only home is targeted
        captured = []

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured.append(req.full_url)
            return FakeResp()

        self.mod._load_env_var = lambda key, fallback="": {
            "DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_HOME_CHANNEL": "456",
        }.get(key, fallback)
        old_origin = os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        os.environ["HERMES_SESSION_CHAT_ID"] = "40196666064944@lid"  # whatsapp
        old = self.mod.urllib.request.urlopen
        self.mod.urllib.request.urlopen = fake_urlopen
        try:
            self.mod._send_discord_notification_sync(self.mod.ACHIEVEMENT_DEFS["first_steps"])
        finally:
            self.mod.urllib.request.urlopen = old
            if old_origin is not None:
                os.environ["HERMES_SESSION_CHAT_ID"] = old_origin
            else:
                os.environ.pop("HERMES_SESSION_CHAT_ID", None)
        # Only the home channel POST — no bogus origin POST
        self.assertEqual(len(captured), 1)
        self.assertIn("/channels/456/messages", captured[0])


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
                         {"pre_llm_call", "pre_tool_call",
                          "transform_terminal_output", "transform_tool_result",
                          "post_llm_call", "post_api_request",
                          "pre_api_request",
                          "post_tool_call",
                          "on_session_start", "on_session_end", "on_session_reset",
                          "on_session_finalize", "subagent_stop", "subagent_start",
                          "post_approval_response", "pre_approval_request",
                          "api_request_error", "pre_gateway_dispatch"})
        cmd_names = {n for n, _ in ctx.commands}
        self.assertEqual(cmd_names, {"achievements", "achievement"})
        # Handlers are the real functions, not lambdas
        self.assertIs(ctx.hooks[0][1], self.mod._pre_llm_call)


class TestCommandHandlers(HookTestBase):
    """Slash command output (achievements/achievement/lang)."""

    def test_achievements_list_all_groups(self):
        out = self.mod._handle_achievements("")
        for g in self.mod.GROUPS:
            self.assertIn(g, out)
        self.assertIn("Hermes Achievements", out)
        self.assertIn("0/16", out)  # Getting Started progress summary

    def test_achievements_unknown_args_fall_through_to_default(self):
        # Unrecognized args must fall through to the default view, not crash
        for arg in ("foo", "bogus_group"):
            out = self.mod._handle_achievements(arg)
            self.assertIn("Hermes Achievements", out, f"arg {arg!r}")
        # Uppercase known commands still work (args are case-insensitive)
        out = self.mod._handle_achievements("STATS")
        self.assertIn("Achievement Stats", out)
        self.mod._set_progress("terminal_jockey", 5, 25)
        out = self.mod._handle_achievements("Next")
        self.assertIn("Next Up", out)

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

    def test_all_views_under_discord_limit_all_locales(self):
        # Every view (default/recent/next/stats/groups) in every locale must
        # stay under Discord's 2000-char cap — guards against locale string
        # growth and newly_unlocked unbounded rendering. This is the WORST
        # case: all 153 achievements unlocked + every stats counter populated
        # (including the longest locales). A stats view that fits when empty
        # but overflows when full is a regression.
        state = self.mod._load_state()
        for aid in self.mod.ACHIEVEMENT_DEFS:
            self.mod._unlock(aid)
        state["newly_unlocked"] = list(self.mod.ACHIEVEMENT_DEFS.keys())[:20]
        stats = state.setdefault("stats", {})
        stats.update({
            "messages": 500,
            "tools_used": {"terminal": 400, "web_search": 100, "web_extract": 50,
                           "read_file": 200, "write_file": 80, "patch": 60,
                           "search_files": 40, "delegate_task": 25,
                           "cronjob": 15, "execute_code": 10, "memory": 5,
                           "skill_manage": 5, "browser_navigate": 10, "todo": 10},
            "total_tokens": 10_000_000, "fast_responses": 25,
            "current_streak": 30, "longest_streak": 30, "config_changes": 15,
            "skills_installed": 15, "skills_created": 5, "plugins_enabled": 5,
            "profiles_created": 5, "cron_jobs_created": 15,
            "mcp_servers_connected": 3, "yolo_tasks": 25, "session_resumes": 10,
            "approvals_always": 5, "approvals_denied": 3,
            "approval_requests": 40, "approvals_gateway": 12, "api_errors": 10,
            "session_resets": 8, "conversations_started": 100,
            "total_sessions": 30, "subagents_spawned": 25, "subagents_failed": 5,
            "max_concurrent_subagents": 3, "peak_tools_per_response": 20,
            "peak_terminal_output_bytes": 2_000_000,
            "peak_tool_result_bytes": 11_000_000, "longest_response_words": 6000,
            "truncated_responses": 25, "longest_subagent_ms": 3_700_000,
            "tool_interrupts": 15, "tool_blocks": 10, "max_retry_depth": 4,
            "approved_patterns": {f"class-{i}" for i in range(25)},
            "platforms": {"discord", "telegram", "whatsapp", "cli", "matrix"},
            "models_used": {f"m{i}" for i in range(10)},
            "providers_used": {"openai", "openrouter", "anthropic", "xai", "local"},
            "slash_commands_used": {"achievements", "new", "resume", "config"},
            "hooks_used": {"pre_tool_call", "post_tool_call", "post_llm_call"},
            "users_seen": {f"u{i}" for i in range(10)},
            "env_types": {"local", "docker", "ssh"},
            "local_requests": 25, "peak_context_messages": 100,
            "peak_input_tokens": 500_000, "tool_errors": 25,
        })
        views = ["", "recent", "next", "stats",
                 "getting_started", "tools_skills", "power_user",
                 "expert", "milestones", "community"]
        for loc in ("en", "es", "fr", "pt"):
            self.mod._handle_lang(loc)
            for v in views:
                out = self.mod._handle_achievements(v)
                self.assertLessEqual(
                    len(out), 2000,
                    f"view {v!r} in {loc} is {len(out)} chars",
                )

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
        self.mod._on_approval_response(
            command="cmd", description="d", pattern_key="k",
            pattern_keys=["k"], session_key="s", surface="gateway",
            choice="once",
        )
        self.mod._on_session_reset(session_id="new", platform="discord")
        out = self.mod._handle_achievements("stats")
        self.assertIn("Subagents spawned:", out)
        self.assertIn("Permanent approvals:", out)
        self.assertIn("Approvals denied:", out)
        self.assertIn("Session resets:", out)
        self.assertIn("Remote approvals:", out)
        self.assertIn("Danger classes approved:", out)
        # 3 children, not the legacy parallel_spawns counter
        self.assertIn("3", out)

    def test_achievements_stats_shows_v24_hook_counters(self):
        # subagent_start, pre_approval_request, api_request_error surface
        for _ in range(3):
            self.mod._on_subagent_start(child_session_id="c")
        for _ in range(4):
            self.mod._on_approval_request(
                command="cmd", description="d", pattern_key="k",
                session_key="s", surface="gateway",
            )
        for _ in range(3):
            self.mod._on_api_request_error(
                error_type="Timeout", error_message="slow",
                status_code=429, retry_count=1, retryable=True,
            )
        out = self.mod._handle_achievements("stats")
        self.assertIn("Peak concurrent agents:", out)
        self.assertIn("Approvals requested:", out)
        self.assertIn("LLM API errors survived:", out)
        self.assertIn("4", out)  # approval requests
        self.assertIn("3", out)  # peak concurrency + api errors

    def test_achievements_stats_shows_users_seen(self):
        self.mod._on_pre_gateway_dispatch(
            event=self._gw_event("discord", "u1"), gateway=None, session_store=None,
        )
        self.mod._on_pre_gateway_dispatch(
            event=self._gw_event("discord", "u2"), gateway=None, session_store=None,
        )
        out = self.mod._handle_achievements("stats")
        self.assertIn("Distinct users seen:", out)
        self.assertIn("2", out)

    def _gw_event(self, platform, user_id):
        class _Source:
            pass
        class _Event:
            pass
        src = _Source()
        src.platform = platform
        src.user_id = user_id
        src.user_name = None
        src.is_bot = False
        ev = _Event()
        ev.internal = False
        ev.source = src
        return ev

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

    def test_achievement_detail_multiple_capped_at_10(self):
        # A generic query (e.g. 'the') can match dozens — output must stay
        # under Discord's 2000-char cap with 'and N more' suffix
        out = self.mod._handle_achievement_detail("the")
        self.assertLessEqual(len(out), 2000, f"multiple list is {len(out)} chars")
        self.assertIn("more", out)
        self.assertIn("Be more specific", out)

    def test_achievement_detail_unknown(self):
        out = self.mod._handle_achievement_detail("not_an_achievement")
        self.assertIn("Unknown", out)

    def test_achievement_detail_empty_usage(self):
        out = self.mod._handle_achievement_detail("")
        self.assertIn("Usage:", out)

    def test_achievements_stats_shows_platforms(self):
        self.turn("hi", model="m1", platform="telegram")
        out = self.mod._handle_achievements("stats")
        self.assertIn("Platforms:", out)
        self.assertIn("telegram", out)

    def test_gw_dispatch_no_source_is_noop(self):
        class _Event:
            internal = False
            source = None
        self.mod._on_pre_gateway_dispatch(
            event=_Event(), gateway=None, session_store=None)
        self.assertEqual(len(self.stats().get("users_seen", set())), 0)

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

    def test_lang_aliases(self):
        # Natural-language aliases resolve to the right code
        for alias, code in [("spanish", "es"), ("français", "fr"),
                            ("francais", "fr"), ("portugues", "pt")]:
            self.mod._handle_lang(alias)
            self.assertEqual(self.mod._load_state()["locale"], code,
                             f"alias {alias!r} should map to {code}")
        self.mod._handle_lang("en")

    def test_lang_invalid_code(self):
        out = self.mod._handle_lang("xx")
        self.assertIn("Unsupported language", out)  # ui.lang_invalid

    def test_group_view_badge_shows_progress(self):
        # A locked achievement with progress renders a compact count in the
        # group view (current/target — descriptions live in /achievement)
        self.mod._set_progress("terminal_jockey", 20, 25)
        out = self.mod._handle_achievements("tools_skills")
        self.assertIn("Terminal Jockey", out)
        self.assertIn("20/25", out)

    def test_group_view_compact_no_description(self):
        # Compact group rendering drops descriptions so big groups stay
        # under Discord's 2000-char cap in every locale
        self.mod._set_progress("terminal_jockey", 20, 25)
        out = self.mod._handle_achievements("tools_skills")
        self.assertNotIn("Run 25 terminal commands", out)  # description

    def test_stats_models_shows_more_suffix(self):
        # More than 3 models → "and N more" suffix
        st = self.mod._load_state()["stats"]
        st["models_used"] = {"m1", "m2", "m3", "m4", "m5"}
        out = self.mod._handle_achievements("stats")
        self.assertIn("m1", out)
        self.assertIn("more", out.lower())

    def test_stats_providers_shown(self):
        # Providers line appears in the stats view once providers are known
        st = self.mod._load_state()["stats"]
        st["providers_used"] = {"openrouter", "openai"}
        out = self.mod._handle_achievements("stats")
        self.assertIn("openrouter", out)
        self.assertIn("openai", out)

    def test_stats_providers_shows_more_suffix(self):
        # More than 3 providers → "and N more" suffix
        st = self.mod._load_state()["stats"]
        st["providers_used"] = {"p1", "p2", "p3", "p4"}
        out = self.mod._handle_achievements("stats")
        self.assertIn("p1", out)
        self.assertIn("more", out.lower())

    def test_lang_show_current_without_args(self):
        out = self.mod._handle_lang("")
        self.assertIn("English", out)

    def test_achievements_lang_passthrough(self):
        out = self.mod._handle_achievements("lang es")
        self.assertIn("Español", out)
        self.assertEqual(self.mod._load_state()["locale"], "es")
        self.mod._handle_lang("en")

    def test_recent_empty(self):
        out = self.mod._handle_achievements("recent")
        self.assertIn("no achievements", out.lower() or "No achievements")

    def test_set_progress_skips_unlocked(self):
        self.mod._unlock("terminal_jockey")
        self.mod._set_progress("terminal_jockey", 5, 25)
        st = self.mod._load_state()["achievements"]["terminal_jockey"]
        self.assertTrue(st["unlocked"])
        self.assertNotIn("progress", st)  # no progress clobber after unlock

    def test_t_with_none_locale(self):
        out = self.mod._t("achievement.first_steps.name", None)
        self.assertEqual(out, "First Steps")

    def test_t_unknown_key_returns_raw_key(self):
        out = self.mod._t("achievement.nonexistent.name", "en")
        self.assertEqual(out, "achievement.nonexistent.name")

    def test_t_invalid_locale_falls_back_to_english(self):
        out = self.mod._t("achievement.first_steps.name", "xx")
        self.assertEqual(out, "First Steps")

    def test_t_format_error_returns_unformatted(self):
        # Missing format arg → unformatted string, not an exception
        out = self.mod._t("ui.stats_cron", "en")
        self.assertEqual(out, self.mod._load_locales()["en"]["ui"]["stats_cron"])

    def test_t_format_kwargs_mismatch_returns_unformatted(self):
        # kwargs given but placeholder missing → unformatted, no exception
        out = self.mod._t("ui.stats_cron", "en", wrong_key=42)
        self.assertEqual(out, self.mod._load_locales()["en"]["ui"]["stats_cron"])

    def test_t_non_dict_intermediate_returns_key(self):
        # A malformed locale where a key maps to a non-dict must not crash
        cache = self.mod._load_locales()
        cache["en"]["achievement"] = "not-a-dict"
        out = self.mod._t("achievement.first_steps.name", "en")
        self.assertEqual(out, "achievement.first_steps.name")
        # Restore for other tests
        cache.pop("en", None)
        self.mod._locales_cache = {}

    def test_default_view_shows_recently_unlocked_section(self):
        # Unlock something in this session, then view the default list
        self.mod._unlock("first_steps")
        out = self.mod._handle_achievements("")
        self.assertIn("First Steps", out)  # recent-unlocked section renders

    def test_detail_view_shows_progress_bar_for_locked_with_progress(self):
        self.mod._set_progress("terminal_jockey", 5, 25)
        out = self.mod._handle_achievement_detail("terminal_jockey")
        self.assertIn("20%", out)  # 5/25 progress rendered
        self.assertIn("█", out)  # progress bar present

    def test_detail_view_shows_plain_locked_without_progress(self):
        out = self.mod._handle_achievement_detail("first_config")
        self.assertNotIn("%", out)
        self.assertIn("Locked", out)

    def test_recent_falls_back_to_unlocked_at_when_newly_empty(self):
        # newly_unlocked empty but achievements exist → sorted by unlocked_at
        self.mod._unlock("first_steps")
        self.mod._unlock("terminal_jockey")
        self.mod._state["newly_unlocked"] = []
        out = self.mod._handle_achievements("recent")
        self.assertIn("First Steps", out)
        self.assertIn("Terminal Jockey", out)

    def test_progress_bar_zero_target(self):
        self.assertEqual(self.mod._progress_bar(5, 0), "░" * 10)

    def test_progress_bar_clamps_at_width(self):
        self.assertEqual(self.mod._progress_bar(50, 10), "█" * 10)

    def test_next_up_empty_state(self):
        out = self.mod._handle_achievements("next")
        self.assertIn("No progress tracked yet", out)

    def test_default_view_shows_closest_to_unlock_hint(self):
        # Progress toward an achievement → default view teases it
        self.mod._set_progress("terminal_jockey", 20, 25)
        out = self.mod._handle_achievements("")
        self.assertIn("Closest to unlock", out)
        self.assertIn("Terminal Jockey", out)

    def test_next_hint_omits_secrets_and_unlocked(self):
        # Secret achievements must not leak into the teaser
        self.mod._set_progress("quick_draw", 2, 5)  # secret
        self.mod._unlock("first_steps")
        hint = self.mod._next_up_hint(self.mod._load_state())
        self.assertNotIn("Quick Draw", hint)
        self.assertNotIn("First Steps", hint)

    def test_next_hint_empty_when_nothing_in_progress(self):
        hint = self.mod._next_up_hint(self.mod._load_state())
        self.assertEqual(hint, "")

    def test_next_up_skips_achievements_meeting_threshold(self):
        # Progress >= target but unlock hasn't fired → excluded from next-up
        self.mod._set_progress("terminal_jockey", 25, 25)
        out = self.mod._handle_achievements("next")
        self.assertNotIn("Terminal Jockey", out)

    def test_next_hint_skips_achievements_meeting_threshold(self):
        self.mod._set_progress("terminal_jockey", 25, 25)
        hint = self.mod._next_up_hint(self.mod._load_state())
        self.assertEqual(hint, "")

    def test_next_up_hint_and_view_agree_on_pct_ties(self):
        # Two achievements at identical progress: the one-line hint and
        # the top of the full next-up view must name the SAME achievement
        # (stable sort keeps ACHIEVEMENT_DEFS order on ties — the hint
        # used to pick def-first while the view broke ties by aid string,
        # a latent divergence this refactor removed).
        self.mod._set_progress("terminal_jockey", 5, 25)  # 20%
        self.mod._set_progress("config_guru", 3, 15)      # 20%
        hint = self.mod._next_up_hint(self.mod._load_state())
        out = self.mod._handle_achievements("next")
        # terminal_jockey is defined before config_guru → stable-tie winner
        self.assertIn("Terminal Jockey", hint)
        self.assertNotIn("Config Guru", hint)
        # The full view lists both (both are top-3 at 20%), but the
        # stable-tie winner must appear FIRST — same ordering as the hint.
        self.assertLess(out.index("Terminal Jockey"), out.index("Config Guru"))

    def test_stats_shows_tier_progress_counters(self):
        # Cron/skills/config counters surface in the stats view
        st = self.mod._load_state()["stats"]
        st["cron_jobs_created"] = 2
        st["skills_created"] = 1
        st["config_changes"] = 3
        out = self.mod._handle_achievements("stats")
        self.assertIn("Cron jobs created:", out)
        self.assertIn("Skills created:", out)
        self.assertIn("Config changes:", out)

    def test_stats_shows_hooks_used_counter(self):
        # Hook Master's counter (distinct hooks authored in plugins) shows
        st = self.mod._load_state()["stats"]
        st["hooks_used"] = {"post_tool_call", "on_session_start", "api_request_error"}
        out = self.mod._handle_achievements("stats")
        self.assertIn("Plugin hooks authored:", out)
        self.assertIn("3", out)

    def test_stats_shows_tokens_consumed(self):
        # Token dimension (post_api_request) appears in the stats view
        st = self.mod._load_state()["stats"]
        st["total_tokens"] = 1234567
        out = self.mod._handle_achievements("stats")
        self.assertIn("Tokens consumed:", out)
        self.assertIn("1234567", out)

    def test_stats_shows_media_messages(self):
        st = self.mod._load_state()["stats"]
        st["media_messages"] = 12
        out = self.mod._handle_achievements("stats")
        self.assertIn("Media messages:", out)
        self.assertIn("12", out)

    def test_stats_shows_peak_context(self):
        st = self.mod._load_state()["stats"]
        st["peak_context_messages"] = 67
        out = self.mod._handle_achievements("stats")
        self.assertIn("Deepest context:", out)
        self.assertIn("67", out)

    def test_stats_shows_peak_input_tokens(self):
        st = self.mod._load_state()["stats"]
        st["peak_input_tokens"] = 250000
        out = self.mod._handle_achievements("stats")
        self.assertIn("Peak input tokens:", out)
        self.assertIn("250000", out)

    def test_stats_shows_local_requests(self):
        st = self.mod._load_state()["stats"]
        st["local_requests"] = 8
        out = self.mod._handle_achievements("stats")
        self.assertIn("Local endpoint calls:", out)
        self.assertIn("8", out)

    def test_stats_shows_longest_message(self):
        st = self.mod._load_state()["stats"]
        st["longest_message_words"] = 340
        out = self.mod._handle_achievements("stats")
        self.assertIn("Longest message:", out)
        self.assertIn("340", out)

    def test_stats_shows_conversations_started(self):
        st = self.mod._load_state()["stats"]
        st["conversations_started"] = 4
        out = self.mod._handle_achievements("stats")
        self.assertIn("Conversations started:", out)
        self.assertIn("4", out)

    def test_stats_shows_peak_batch(self):
        st = self.mod._load_state()["stats"]
        st["peak_tools_per_response"] = 9
        out = self.mod._handle_achievements("stats")
        self.assertIn("Peak tools per response:", out)
        self.assertIn("9", out)

    def test_stats_shows_peak_terminal_output(self):
        st = self.mod._load_state()["stats"]
        st["peak_terminal_output_bytes"] = 150 * 1024
        out = self.mod._handle_achievements("stats")
        self.assertIn("Biggest command output:", out)
        self.assertIn("150.0 KiB", out)

    def test_stats_shows_peak_tool_result(self):
        st = self.mod._load_state()["stats"]
        st["peak_tool_result_bytes"] = 2 * 1024 * 1024
        out = self.mod._handle_achievements("stats")
        self.assertIn("Biggest tool result:", out)
        self.assertIn("2.0 MiB", out)

    def test_stats_shows_env_types(self):
        st = self.mod._load_state()["stats"]
        st["env_types"] = {"local", "docker"}
        out = self.mod._handle_achievements("stats")
        self.assertIn("Environments used:", out)
        self.assertIn("2", out)
        self.assertIn("docker", out)
        self.assertIn("local", out)

    def test_stats_shows_longest_response(self):
        st = self.mod._load_state()["stats"]
        st["longest_response_words"] = 1234
        out = self.mod._handle_achievements("stats")
        self.assertIn("Longest model response:", out)
        self.assertIn("1234", out)

    def test_stats_shows_truncations(self):
        st = self.mod._load_state()["stats"]
        st["truncated_responses"] = 4
        out = self.mod._handle_achievements("stats")
        self.assertIn("Truncated responses:", out)
        self.assertIn("4", out)

    def test_stats_shows_interrupts(self):
        st = self.mod._load_state()["stats"]
        st["tool_interrupts"] = 3
        out = self.mod._handle_achievements("stats")
        self.assertIn("Tool calls interrupted:", out)
        self.assertIn("3", out)

    def test_stats_shows_blocks(self):
        st = self.mod._load_state()["stats"]
        st["tool_blocks"] = 2
        out = self.mod._handle_achievements("stats")
        self.assertIn("Tool calls blocked:", out)
        self.assertIn("2", out)

    def test_stats_hidden_when_absent(self):
        # New status stats must not render at zero — same rule as every
        # other stat line (only non-zero activity surfaces).
        out = self.mod._handle_achievements("stats")
        self.assertNotIn("Tool calls interrupted:", out)
        self.assertNotIn("Tool calls blocked:", out)
        self.assertNotIn("Deepest API retry depth:", out)

    def test_stats_shows_max_retry_depth(self):
        st = self.mod._load_state()["stats"]
        st["max_retry_depth"] = 4
        out = self.mod._handle_achievements("stats")
        self.assertIn("Deepest API retry depth:", out)
        self.assertIn("4", out)

    def test_stats_shows_longest_subagent(self):
        st = self.mod._load_state()["stats"]
        st["longest_subagent_ms"] = 12 * 60 * 1000 + 30 * 1000
        out = self.mod._handle_achievements("stats")
        self.assertIn("Longest subagent:", out)
        self.assertIn("12m 30s", out)

    def test_stats_new_dimensions_hidden_when_absent(self):
        out = self.mod._handle_achievements("stats")
        self.assertNotIn("Media messages:", out)
        self.assertNotIn("Deepest context:", out)
        self.assertNotIn("Longest message:", out)
        self.assertNotIn("Peak input tokens:", out)
        self.assertNotIn("Local endpoint calls:", out)
        self.assertNotIn("Conversations started:", out)
        self.assertNotIn("Peak tools per response:", out)
        self.assertNotIn("Biggest command output:", out)
        self.assertNotIn("Biggest tool result:", out)
        self.assertNotIn("Environments used:", out)
        self.assertNotIn("Longest model response:", out)
        self.assertNotIn("Truncated responses:", out)
        self.assertNotIn("Longest subagent:", out)

    def test_stats_completionist_unlocked_line(self):
        # All achievements unlocked → completionist line appears
        for aid in self.mod.ACHIEVEMENT_DEFS:
            self.mod._unlock(aid)
        out = self.mod._handle_achievements("stats")
        self.assertIn("COMPLETIONIST UNLOCKED!", out)

    def test_recent_uses_newly_when_present(self):
        self.mod._unlock("first_steps")
        self.mod._unlock("terminal_jockey")
        self.mod._state["newly_unlocked"] = ["first_steps", "terminal_jockey"]
        out = self.mod._handle_achievements("recent")
        self.assertIn("First Steps", out)
        self.assertIn("Terminal Jockey", out)

    def test_recent_caps_output_at_10(self):
        # 20 new unlocks → recent view shows at most 10 (Discord 2000-char cap)
        ids = list(self.mod.ACHIEVEMENT_DEFS.keys())[:20]
        state = self.mod._load_state()
        state["newly_unlocked"] = ids
        for aid in ids:
            self.mod._unlock(aid)
        out = self.mod._handle_achievements("recent")
        self.assertLessEqual(len(out), 2000, f"recent view is {len(out)} chars")
        # First (oldest) of the 20 must be excluded; last 10 present
        first_name = self.mod._t(f"achievement.{ids[0]}.name", "en")
        self.assertNotIn(first_name, out)


class TestSecretAchievements(HookTestBase):
    """Locked secret achievements hide name/description/progress."""

    def setUp(self):
        super().setUp()
        # sanity: the defs really do declare secrets
        secrets = [aid for aid, adef in self.mod.ACHIEVEMENT_DEFS.items()
                   if adef.get("secret") or adef.get("hidden")]
        self.secrets = secrets
        self.assertGreaterEqual(len(secrets), 3)

    def test_locked_secret_badge_is_masked(self):
        out = self.mod._handle_achievements("power_user")
        self.assertIn("???", out)
        for aid in self.secrets:
            adef = self.mod.ACHIEVEMENT_DEFS[aid]
            if adef["group"] == "Power User":
                self.assertNotIn(adef["name"], out)
                self.assertNotIn(adef["description"], out)

    def test_unlocked_secret_badge_is_revealed(self):
        # pick a Power User secret for the power_user view
        pu_secret = next(aid for aid in self.secrets
                         if self.mod.ACHIEVEMENT_DEFS[aid]["group"] == "Power User")
        self.mod._unlock(pu_secret)
        self.mod._state["newly_unlocked"] = []
        adef = self.mod.ACHIEVEMENT_DEFS[pu_secret]
        out = self.mod._handle_achievements("power_user")
        # Compact group view reveals the name (description lives in detail)
        self.assertIn(adef["name"], out)
        detail = self.mod._handle_achievement_detail(pu_secret)
        self.assertIn(adef["description"], detail)

    def test_locked_secret_detail_is_masked(self):
        for aid in self.secrets:
            out = self.mod._handle_achievement_detail(aid)
            self.assertIn("???", out)
            self.assertNotIn(self.mod.ACHIEVEMENT_DEFS[aid]["description"], out)

    def test_unlocked_secret_detail_is_revealed(self):
        aid = self.secrets[0]
        self.mod._unlock(aid)
        out = self.mod._handle_achievement_detail(aid)
        self.assertIn(self.mod.ACHIEVEMENT_DEFS[aid]["name"], out)
        self.assertIn(self.mod.ACHIEVEMENT_DEFS[aid]["description"], out)

    def test_locked_secret_never_in_next_up(self):
        # give every non-secret a tiny bit of progress, then check secrets absent
        for aid, adef in self.mod.ACHIEVEMENT_DEFS.items():
            if adef.get("secret") or adef.get("hidden"):
                continue
            self.mod._set_progress(aid, 1, 10)
        out = self.mod._handle_achievements("next")
        for aid in self.secrets:
            self.assertNotIn(self.mod.ACHIEVEMENT_DEFS[aid]["name"], out)


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
        # Regenerate the derived sections in place and require zero diff —
        # the committed README must already be what the renderer produces
        # (a local mirror of the CI git-diff gate, so drift fails pytest
        # instead of only the workflow).
        import subprocess
        import sys as _sys
        script = os.path.join(PLUGIN_DIR, "scripts", "render_readme.py")
        with open(os.path.join(PLUGIN_DIR, "README.md"), encoding="utf-8") as f:
            before = f.read()
        result = subprocess.run([_sys.executable, script], capture_output=True, text=True,
                                cwd=PLUGIN_DIR, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK: 153 achievements", result.stdout)
        with open(os.path.join(PLUGIN_DIR, "README.md"), encoding="utf-8") as f:
            after = f.read()
        self.assertEqual(after, before,
                         "README.md is out of sync — run: python scripts/render_readme.py")

    def test_example_block_matches_defs(self):
        # The example-output block is illustrative, but every DERIVED
        # number (group denominators, next-up thresholds/bars/percents,
        # the closest-to-unlock hint) must match the recognition maps.
        # These rotted twice before the renderer owned them (group sizes
        # 10→16/23→44/18→40/15→19 and Deep Diver 2/5 after the def moved
        # to 25 web searches). Numerators are hand-picked; this test
        # validates everything derived from them.
        with open(os.path.join(PLUGIN_DIR, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        example = readme[readme.index("### Example output"):
                         readme.index("### Multi-Language Support")]
        mod = self.mod

        # Group summary lines: {emoji} **{group}** ({num}/{size}) {bar}
        for group in mod.GROUPS:
            size = sum(1 for d in mod.ACHIEVEMENT_DEFS.values() if d["group"] == group)
            m = re.search(
                rf"^{re.escape(mod.GROUP_EMOJIS[group])} \*\*{re.escape(group)}\*\* \((\d+)/(\d+)\) ([█░]+)$",
                example, re.MULTILINE)
            self.assertIsNotNone(m, f"example block missing group line for '{group}'")
            num, den, bar = int(m.group(1)), int(m.group(2)), m.group(3)
            self.assertEqual(den, size, f"example denominator for '{group}' stale")
            self.assertEqual(bar, mod._progress_bar(num, size),
                             f"example bar for '{group}' stale")

        # Recognition thresholds: aid → lowest threshold across all maps
        thresholds = {}
        for const in ("_TOOL_THRESHOLDS", "_TOTAL_TOOL_THRESHOLDS",
                      "_MESSAGE_THRESHOLDS", "_COUNTER_THRESHOLDS"):
            value = getattr(mod, const, {}) or {}
            entries = value.items() if isinstance(value, dict) else [(None, value)]
            for _stat, tiers in entries:
                for threshold, aid in tiers:
                    if aid not in thresholds or threshold < thresholds[aid]:
                        thresholds[aid] = threshold

        # Next-up lines: {color} **{name}** — {bar} {cur}/{tgt} ({pct}%)
        for name in ("Deep Diver", "Config Guru", "Terminal Jockey"):
            aid = next((a for a, d in mod.ACHIEVEMENT_DEFS.items() if d["name"] == name), None)
            self.assertIsNotNone(aid, f"example next-up references unknown achievement '{name}'")
            tgt = thresholds[aid]
            color = mod.RARITY_EMOJIS.get(mod.ACHIEVEMENT_DEFS[aid]["rarity"], "⬜")
            m = re.search(
                rf"^{re.escape(color)} \*\*{re.escape(name)}\*\* — ([█░]+) (\d+)/(\d+) \((\d+)%\)$",
                example, re.MULTILINE)
            self.assertIsNotNone(m, f"example block missing next-up line for '{name}'")
            bar, cur, den, pct = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
            self.assertEqual(den, tgt, f"example threshold for '{name}' stale")
            self.assertTrue(0 < cur < tgt, f"example progress for '{name}' implausible")
            self.assertEqual(bar, mod._progress_bar(cur, tgt),
                             f"example bar for '{name}' stale")
            self.assertEqual(pct, int(cur / tgt * 100),
                             f"example percent for '{name}' stale")

        # Closest-to-unlock hint mirrors the top next-up entry
        name = "Deep Diver"
        aid = next((a for a, d in mod.ACHIEVEMENT_DEFS.items() if d["name"] == name), None)
        tgt = thresholds[aid]
        m = re.search(
            rf"^🔮 Closest to unlock: \*\*{re.escape(name)}\*\* ([█░]+) (\d+)/(\d+) \((\d+)%\)$",
            example, re.MULTILINE)
        self.assertIsNotNone(m, "example block missing closest-to-unlock hint")
        bar, cur, den, pct = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        self.assertEqual(den, tgt, "closest-to-unlock threshold stale")
        self.assertEqual(bar, mod._progress_bar(cur, tgt), "closest-to-unlock bar stale")
        self.assertEqual(pct, int(cur / tgt * 100), "closest-to-unlock percent stale")

    def test_health_check_script_passes(self):
        # The health check must pass against the repo checkout (defs,
        # locales, manifest↔register agreement, detection maps). Guards
        # against the exact drift failures it was written to catch.
        import subprocess
        import sys as _sys
        script = os.path.join(PLUGIN_DIR, "scripts", "check_plugin.py")
        result = subprocess.run([_sys.executable, script], capture_output=True, text=True,
                                cwd=PLUGIN_DIR, check=False)
        self.assertEqual(
            result.returncode, 0,
            f"check_plugin.py failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("Plugin healthy", result.stdout)

    def test_health_check_gateway_contract_flag(self):
        # --gateway validates the hook kwarg contract against the installed
        # Hermes source. In CI (no Hermes source) it must skip gracefully
        # with exit 0; on the deployment host it must pass fully.
        import subprocess
        import sys as _sys
        script = os.path.join(PLUGIN_DIR, "scripts", "check_plugin.py")
        result = subprocess.run([_sys.executable, script, "--gateway"],
                                capture_output=True, text=True,
                                cwd=PLUGIN_DIR, check=False)
        self.assertEqual(
            result.returncode, 0,
            f"check_plugin.py --gateway failed:\n{result.stdout}\n{result.stderr}",
        )
        self.assertIn("Plugin healthy", result.stdout)
        self.assertIn("Hook kwarg contract", result.stdout)


class TestEveryAchievementUnlockable(HookTestBase):
    """Full-grind simulation: prove all 153 achievement defs can unlock.

    After several rounds of achievement swaps (v2.3.0, v2.4.0, v2.4.1),
    a def could sit in a detection map with an impossible condition (wrong
    threshold, typo'd key, or no detection path at all) and no test would
    catch it. This test drives every hook with escalating synthetic data —
    the same kwargs the gateway passes — and asserts EVERY def unlocks.
    A dead achievement fails the run with its ID listed.
    """

    def setUp(self):
        super().setUp()
        # Hermetic: never touch real Discord during the grind
        os.environ.pop("DISCORD_BOT_TOKEN", None)
        os.environ.pop("DISCORD_HOME_CHANNEL", None)

    def _gateway_event(self, platform, user_id, media=False, text=""):
        class _Source:
            pass

        class _Event:
            def __init__(self, txt):
                self._text = txt

            @property
            def text(self):
                return self._text

            def is_command(self):
                return bool(self._text.startswith("/"))

            def get_command(self):
                if not self.is_command():
                    return None
                parts = self._text.split(maxsplit=1)
                raw = parts[0][1:].lower() if parts else None
                if raw and "/" in raw:
                    return None
                return raw

        src = _Source()
        src.platform = platform
        src.user_id = user_id
        src.user_name = f"User {user_id}"
        src.is_bot = False
        ev = _Event(text)
        ev.internal = False
        ev.source = src
        ev.media_urls = ["/tmp/pic.jpg"] if media else []
        ev.media_types = ["image/jpeg"] if media else []
        ev.message_type = "photo" if media else "text"
        return ev

    def _grind(self):
        """Drive all 13 hooks with realistic synthetic gateway data."""
        import datetime as _dt
        from datetime import date, timedelta
        from unittest.mock import patch as _patch

        mod = self.mod

        # Time control: 3:30 AM local → Early Bird + Night Owl both fire.
        class FakeDT(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 7, 15, 3, 30, 0, tzinfo=tz)

        # _save_state is exercised by dedicated tests; a no-op keeps the
        # 2000+ hook calls fast while detection still mutates in-memory state.
        with _patch.object(mod, "datetime", FakeDT), \
             _patch.object(mod, "_save_state", lambda force=False: None):
            # ── Sessions first, so Persistent sees total_sessions=3 ──
            for sid in ("g-s1", "g-s2", "g-s3"):
                mod._on_session_start(session_id=sid)

            # ── Turn grind: 1050 turns across models/platforms/commands ──
            models = [f"model-{i}" for i in range(12)]
            platforms = ["discord", "whatsapp", "telegram", "cli", "matrix"]
            commands = [
                "hello",
                "hermes config set theme dark",
                "hermes doctor",
                "hermes mcp add my-server",
                "hermes profile create work",
                "hermes plugins enable achievements",
                "hermes changelog",
                "hermes config get model",
                "cron 2026-07-20T09:00:00 one-shot",
                "hermes docs",
                "hermes mcp config server",
                "/resume",
                "--yolo",
                "hermes config set env_file .env",
                "¡Hola! ¿Cómo estás? 你好",
                "hermes skills install foo",
                "/achievements list",
                "/new",
                "hermes changelog release notes",
                "hermes update --check",
            ]
            for i in range(1050):
                cmd = commands[i % len(commands)]
                # A few turns carry a long model response → Essayist (1000)
                # + Novel Author (5000) unlock. Cycle lengths so most turns
                # stay short (no unlock) but 3 responses cross the 5000 bar.
                if i % 350 == 0:
                    resp = ("word " * 5200).strip()
                else:
                    resp = "short reply"
                mod._post_llm_call(
                    user_message=cmd,
                    assistant_response=resp,
                    conversation_history=[{"role": "user", "content": cmd},
                                          {"role": "assistant", "content": resp}],
                    model=models[i % len(models)],
                    platform=platforms[i % len(platforms)],
                )

            # ── Conversation starts: 105 fresh contexts → Icebreaker (1),
            # Conversation Habit (10), Serial Starter (50), Colossus (100).
            # is_first_turn=False on a couple fires exercises the no-op guard.
            for i in range(105):
                mod._pre_llm_call(
                    is_first_turn=(i < 103),
                    session_id="g-conv",
                    task_id="t",
                    turn_id="t",
                    user_message="hi",
                    conversation_history=[],
                    model=models[0],
                    platform="cli",
                )

            # ── Tool batching: one 25-call response → Double Time (2),
            # Batch Artist (5), Parallel Barrage (10), Tool Torrent (20).
            # Same api_request_id = same assistant response. A second
            # response with fewer calls exercises the reset + peak-max.
            for i in range(25):
                mod._pre_tool_call(
                    tool_name="terminal", args={}, task_id="t",
                    session_id="s-main", tool_call_id=f"tc-{i}",
                    turn_id="turn", api_request_id="g-api-big",
                )
            for i in range(3):
                mod._pre_tool_call(
                    tool_name="read_file", args={}, task_id="t",
                    session_id="s-main", tool_call_id=f"tc2-{i}",
                    turn_id="turn", api_request_id="g-api-small",
                )

            # ── Tool grind: one big session covering every tool type ──
            # Counts chosen so every per-tool threshold crosses; read_file
            # at 110 feeds file_artisan (best-of feeding tools, not sum).
            tool_counts = {
                "terminal": 510, "web_search": 30, "web_extract": 30,
                "execute_code": 110, "memory": 110, "read_file": 110,
                "write_file": 40, "patch": 40, "search_files": 40,
                "session_search": 12, "web_scrape": 30,
                "browser_navigate": 3, "browser_click": 3,
                "browser_snapshot": 3, "browser_type": 3,
                "vision_analyze": 3, "delegate_task": 5,
                "skill_manage": 18, "cronjob": 18, "send_message": 3,
                "text_to_speech": 2, "todo": 3, "clarify": 2,
            }
            special_args = {
                "cronjob": {"action": "create", "schedule": "2026-07-20T09:00:00",
                            "workdir": "/tmp/x", "context_from": ["job-1"]},
                "delegate_task": {"tasks": [{"goal": "a"}, {"goal": "b"},
                                            {"goal": "c"}]},
                "skill_manage": {"action": "create"},
                "memory": {"action": "add"},
            }
            for tool, count in tool_counts.items():
                for i in range(count):
                    if tool in special_args:
                        args = special_args[tool]
                    elif tool == "write_file" and i < 3:
                        # Plugin Developer + Hook Master: manifest writes
                        args = {
                            "path": "/plugins/x/plugin.yaml",
                            "content": (
                                "name: test\nhooks:\n"
                                "register_hook('post_tool_call')\n"
                                "register_hook('api_request_error')\n"
                                "register_hook('on_session_start')"
                            ),
                        }
                    else:
                        args = {"command": "echo x"} if tool == "terminal" else {}
                    mod._post_tool_call(
                        tool_name=tool, args=args, session_id="s-main",
                        duration_ms=500,
                    )

            # ── Streaks: 30 consecutive days → Week Warrior + Monthly ──
            mod._on_session_end(session_id="g-end-1")   # streak = 1
            for i in range(29):
                st = mod._load_state()["stats"]
                st["last_active_date"] = (
                    date.today() - timedelta(days=1)  # noqa: DTZ011
                ).isoformat()
                mod._on_session_end(session_id=f"g-end-{i + 2}")

            # ── Subagents: 3 concurrent (Conductor) then 28 stops ──
            # One stop carries a 65-minute duration → Slow Thinker (10m) +
            # Marathon (60m). Others are fast (500ms).
            for i in range(3):
                mod._on_subagent_start(child_role="leaf", child_goal=f"g-{i}")
            for i in range(28):
                role = "orchestrator" if i == 2 else "leaf"
                status = "failed" if i in (0, 1) else "completed"
                dur_ms = 65 * 60 * 1000 if i == 5 else 500
                mod._on_subagent_stop(
                    child_role=role, child_status=status, duration_ms=dur_ms,
                )

            # ── Tool errors: 30 failed calls → Trial and Error ──
            for i in range(30):
                mod._post_tool_call(
                    tool_name="terminal", args={}, session_id="s-main",
                    duration_ms=500, status="error",
                )

            # ── Tool interrupts: 15 user interrupts → Manual Override (1) +
            # Backseat Driver (5) + Control Freak (15). The gateway emits
            # status="cancelled" with error_type keyboard_interrupt when the
            # user presses stop while a tool runs.
            for i in range(15):
                mod._post_tool_call(
                    tool_name="terminal", args={}, session_id="s-main",
                    duration_ms=200, status="cancelled",
                    error_type="keyboard_interrupt",
                )

            # ── Tool blocks: 10 policy blocks → Dead End (1) + Brick Wall (10).
            # status="blocked" fires when scope/plugin/guardrail policy denies
            # a tool BEFORE execution.
            for i in range(10):
                mod._post_tool_call(
                    tool_name="write_file", args={}, session_id="s-main",
                    duration_ms=0, status="blocked",
                    error_type="guardrail_block",
                )

            # ── API requests: token milestones + fast responses + providers ──
            # 12K tokens × 1050 requests = 12.6M → crosses all three token
            # thresholds. Alternate 0.5s (fast) / 9.0s (slow) → 525 fast
            # requests → Speed Demon. Mixed usage shapes exercise both the
            # total_tokens path and the prompt+completion fallback. Cycle
            # 8 providers → Provider Hopper (2) + Provider Collector (5).
            # api_call_count escalates to 14 → Deep Dive (≥10 in one turn).
            # message_count escalates past 50 and 100 → Deep Context +
            # Context Colossus (single-request context depth).
            providers = [f"provider-{i}" for i in range(8)]
            for i in range(1050):
                if i % 3 == 0:
                    usage = {"prompt_tokens": 8000, "completion_tokens": 4000}
                else:
                    usage = {"total_tokens": 12000}
                # finish_reason="length" on 30 requests → Cut Short (1) +
                # Token Wall (25). Others use "stop" (no truncation).
                fr = "length" if i < 30 else "stop"
                mod._post_api_request(
                    usage=usage,
                    api_duration=0.5 if i % 2 == 0 else 9.0,
                    model=models[i % len(models)],
                    provider=providers[i % len(providers)],
                    api_call_count=1 + (i % 14),
                    message_count=20 + (i % 120),
                    finish_reason=fr,
                )

            # ── Preflight: local endpoints + input-token spikes ──
            # 30 localhost requests → Local First + Self-Hosted (25).
            # approx_input_tokens escalates past 200K and 500K → Context
            # Monster + Token Tsunami. Cycle cloud + local base_urls.
            for i in range(60):
                mod._pre_api_request(
                    base_url=("http://localhost:4000/v1" if i % 2 == 0
                              else "https://api.openai.com/v1"),
                    approx_input_tokens=20_000 + (i * 15_000),
                    model=models[i % len(models)],
                    provider=providers[i % len(providers)],
                    api_call_count=1 + (i % 14),
                )

            # ── Transform hooks: raw output volume, env diversity, exit 127,
            # tool-result size. 2MB output → Verbose Output (100KB) + Data
            # Flood (1MB). 5 env types → Multi-Environment + Omnipresent.
            # Exit 127 → Ghost Command. 20MB result → Big Haul + Colossal
            # Result. Extra small fires exercise the no-unlock paths.
            mod._transform_terminal_output(
                command="cat big.log", output="L" * (2 * 1024 * 1024),
                returncode=0, task_id="t", env_type="local",
            )
            for i, env in enumerate(("ssh", "docker", "singularity", "modal",
                                     "daytona")):
                mod._transform_terminal_output(
                    command="env", output="x", returncode=0,
                    task_id="t", env_type=env,
                )
            mod._transform_terminal_output(
                command="definitely-not-a-command", output="",
                returncode=127, task_id="t", env_type="local",
            )
            mod._transform_terminal_output(
                command="ok", output="tiny", returncode=0,
                task_id="t", env_type="local",
            )
            mod._transform_tool_result(
                tool_name="search_files", args={}, result="R" * (20 * 1024 * 1024),
                task_id="t", session_id="s-main", tool_call_id="tc",
                turn_id="turn", api_request_id="g-api-big", duration_ms=500,
                status="ok", error_type=None, error_message=None,
            )
            mod._transform_tool_result(
                tool_name="read_file", args={}, result="small",
                task_id="t", session_id="s-main", tool_call_id="tc2",
                turn_id="turn", api_request_id="g-api-small", duration_ms=500,
                status="ok", error_type=None, error_message=None,
            )

            # ── API errors, approvals, distinct users, media, reset ──
            for i in range(12):
                mod._on_api_request_error(error_type="timeout", status_code=429)
            # Retry depth: one request failing 4× in a row (depths 0-4)
            # → Tenacious (2) + Undeterred (4). The gateway fires the hook
            # once per failed attempt with the current depth.
            for depth in range(5):
                mod._on_api_request_error(
                    error_type="Timeout", status_code=500,
                    retry_count=depth, retryable=True,
                )
            for i in range(12):
                mod._on_approval_request(command="terminal", surface="cli")
            mod._on_approval_response(choice="deny")
            mod._on_approval_response(choice="always")
            # Approval context: 12 gateway-surface approvals → Remote Warden
            # (1) + Long-Distance Operator (10); 30 distinct danger classes
            # approved → Risk Explorer (5) + Danger Collector (15) + Living
            # on the Edge (25). Same classes repeated must NOT double-count.
            for i in range(12):
                mod._on_approval_response(
                    command=f"dangerous cmd {i}", description="d",
                    pattern_key=f"class-{i}", pattern_keys=[f"class-{i}"],
                    session_key="g", surface="gateway", choice="once",
                )
            for i in range(30):
                mod._on_approval_response(
                    command=f"dangerous cmd {i}", description="d",
                    pattern_key=f"class-{i}", pattern_keys=[f"class-{i}"],
                    session_key="g", surface="cli", choice="once",
                )
            for i in range(10):
                mod._on_pre_gateway_dispatch(
                    event=self._gateway_event("discord", f"g-user-{i}"),
                    gateway=None, session_store=None,
                )
            # 30 media messages → Show and Tell (1) + Visual Storyteller (25)
            for i in range(30):
                mod._on_pre_gateway_dispatch(
                    event=self._gateway_event("discord", "g-user-0", media=True),
                    gateway=None, session_store=None,
                )
            # 30 distinct gateway commands from the PRIMARY user (g-user-0)
            # → Slash Commander (3) + Command Center (10) + Command General
            # (25). Commands from OTHER users must not count.
            for i in range(30):
                mod._on_pre_gateway_dispatch(
                    event=self._gateway_event(
                        "discord", "g-user-0", text=f"/gcmd{i}"),
                    gateway=None, session_store=None,
                )
            mod._on_pre_gateway_dispatch(
                event=self._gateway_event("discord", "g-user-1", text="/reset"),
                gateway=None, session_store=None,
            )
            mod._on_session_reset(session_id="g-new")
            mod._on_session_finalize()

            # Long user message (1600 words) → Wordsmith (300) + Novelist (1500)
            long_msg = ("word " * 1600).strip()
            mod._post_llm_call(
                user_message=long_msg,
                conversation_history=[{"role": "user", "content": long_msg}],
                model="model-0", platform="cli",
            )

            # Final turn: re-checks group/rarity completions + completionist
            mod._post_llm_call(
                user_message="one more turn",
                conversation_history=[{"role": "user", "content": "one more turn"}],
                model="model-0", platform="cli",
            )
            mod._check_completionist()

    def test_all_153_achievements_can_unlock(self):
        """Every def in ACHIEVEMENT_DEFS must unlock through real hooks."""
        self._grind()
        state = self.mod._load_state()
        locked = [
            aid for aid in self.mod.ACHIEVEMENT_DEFS
            if not state["achievements"].get(aid, {}).get("unlocked")
        ]
        self.assertEqual(
            locked, [],
            "Unlockable-invariant violated — dead/unreachable achievement defs: "
            f"{locked}",
        )

    def test_completionist_unlocks_as_153rd(self):
        """Completionist requires every other achievement first."""
        self._grind()
        state = self.mod._load_state()
        ach = state["achievements"]["completionist"]
        self.assertTrue(ach["unlocked"])
        unlocked = sum(
            1 for aid in self.mod.ACHIEVEMENT_DEFS
            if state["achievements"].get(aid, {}).get("unlocked")
        )
        self.assertEqual(unlocked, 153)


if __name__ == "__main__":
    unittest.main(verbosity=2)
