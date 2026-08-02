"""
Hermes Achievements Plugin
===========================
Steam-style achievement badges for using and learning about Hermes Agent.
153 achievements across 6 categories.

Hooks:
  - post_llm_call:  detects tool calls from conversation_history → unlocks achievements
                    sends immediate Discord notification on unlock
  - on_session_end: tracks session metadata, streaks, and session-based achievements

Achievement notifications are delivered as standalone Discord messages the
moment they unlock — not appended to the next assistant response.
"""

import functools
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime

# ── Paths ────────────────────────────────────────────────────────────────

_HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
_STATE_PATH = os.path.join(_HERMES_HOME, "achievements", "state.json")
_STATE_BAK_PATH = _STATE_PATH + ".bak"

# ── Env helpers ───────────────────────────────────────────────────────────

def _load_env_var(key, fallback=""):
    """Load a variable from .env file, falling back to os.environ."""
    env_path = os.path.join(_HERMES_HOME, ".env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip().strip("'\"").strip()
    except OSError:
        pass
    return os.environ.get(key, fallback)


# ── Discord delivery ─────────────────────────────────────────────────────

# Burst unlocks (several thresholds crossing in one turn) are batched into
# a single Discord message instead of spamming one message per achievement.
_NOTIF_QUEUE: list = []
_NOTIF_QUEUE_LOCK = threading.Lock()
_NOTIF_DEBOUNCE_S = 3.0
_notif_timer = None


def _session_chat_id():
    """ContextVar-aware HERMES_SESSION_CHAT_ID with os.environ fallback.

    The gateway stores session routing state in task-local ContextVars
    (gateway/session_context.py) precisely because the old process-global
    os.environ values were clobbered by concurrent messages. Reading
    os.environ directly here returns "" in gateway contexts — origin
    notifications would silently never send. Fall back to os.environ only
    when the gateway package isn't importable (CLI/cron/tests).
    """
    try:
        from gateway.session_context import get_session_env
        return get_session_env("HERMES_SESSION_CHAT_ID", "")
    except Exception:
        return os.environ.get("HERMES_SESSION_CHAT_ID", "")


def _send_discord_notification(ach_def):
    """Deliver achievement notification asynchronously (non-blocking).

    Unlocks are debounced: rapid-fire unlocks within the debounce window
    coalesce into ONE batched message (multiple embeds) rather than one
    message per achievement. A daemon timer does the delivery so a slow
    Discord API response never stalls the agent's hook pipeline.
    """
    global _notif_timer
    # Capture the origin channel NOW, in the hook's session context. The
    # flush runs 3s later in a Timer thread whose context has no session
    # vars — reading HERMES_SESSION_CHAT_ID there would always miss.
    entry = {"ach": ach_def, "origin": _session_chat_id()}
    with _NOTIF_QUEUE_LOCK:
        _NOTIF_QUEUE.append(entry)
        if _notif_timer is not None:
            _notif_timer.cancel()
        _notif_timer = threading.Timer(_NOTIF_DEBOUNCE_S, _flush_notification_queue)
        _notif_timer.daemon = True
        _notif_timer.start()


def _flush_notification_queue():
    """Deliver everything queued in the debounce window as one batch."""
    global _notif_timer
    with _NOTIF_QUEUE_LOCK:
        batch = list(_NOTIF_QUEUE)
        _NOTIF_QUEUE.clear()
        _notif_timer = None
    if not batch:
        return
    try:
        _send_discord_notification_batch(batch)
    except Exception as exc:  # noqa: BLE001 — never let a notification thread crash
        import logging
        logging.getLogger(__name__).warning(
            "Achievement notification batch failed: %s", exc
        )


def _send_discord_notification_batch(batch):
    """Send one message per target with all embeds (deduped targets)."""
    token = _load_env_var("DISCORD_BOT_TOKEN")
    if not token or not batch:
        return

    home_channel = _load_env_var("DISCORD_HOME_CHANNEL")
    home_thread = _load_env_var("DISCORD_HOME_CHANNEL_THREAD_ID")

    state = _load_state()
    locale = state.get("locale", "en") if state else "en"

    embeds = []
    for entry in batch:
        ach_def = entry["ach"]
        emoji = RARITY_EMOJIS.get(ach_def.get("rarity", "common"), "⬜")
        ach_name = _t(f"achievement.{ach_def['id']}.name", locale)
        ach_desc = _t(f"achievement.{ach_def['id']}.description", locale)
        rarity_label = _t(f"rarity.{ach_def['rarity']}", locale)
        group_key = ach_def["group"].lower().replace(" & ", "_").replace(" ", "_")
        group_label = _t(f"group.{group_key}", locale)
        embeds.append({
            "title": f"{emoji} {ach_def['emoji']} {ach_name}",
            "description": ach_desc,
            "color": _RARITY_COLORS.get(ach_def.get("rarity", "common"), 0x9CA3AF),
            "footer": {"text": f"{rarity_label} · {group_label}"},
        })

    content = ""
    if len(embeds) > 1:
        content = _t("ui.batch_unlocked", locale, count=len(embeds))
    # Discord caps embeds at 10 per message — chunk larger bursts
    MAX_EMBEDS = 10

    # Build unique target set — dedup home vs origin. Origins were captured
    # per-entry at enqueue time (in the hook's session context); a burst
    # across sessions sends to each distinct origin. Origin is only used
    # when it looks like a Discord channel (numeric snowflake); other
    # platforms (WhatsApp chat IDs, Telegram IDs) would POST to a bogus
    # URL and fail.
    targets = []
    if home_channel:
        targets.append(("home", home_channel, home_thread or None))
    for origin in sorted({entry.get("origin", "") for entry in batch}):
        if origin and origin != home_channel and str(origin).isdigit():
            targets.append(("origin", origin, None))

    for start in range(0, len(embeds), MAX_EMBEDS):
        chunk = embeds[start:start + MAX_EMBEDS]
        chunk_content = content if start == 0 else ""
        payload = json.dumps({"content": chunk_content, "embeds": chunk}).encode()
        for label, channel_id, thread_id in targets:
            # Discord threads: the thread ID IS the channel ID in the API —
            # use the thread when one is configured for the home channel
            url = f"https://discord.com/api/v10/channels/{thread_id or channel_id}/messages"
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={
                        "Authorization": f"Bot {token}",
                        "Content-Type": "application/json",
                        "User-Agent": "DiscordBot/1.0 (achievements-plugin)",
                    },
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to send achievement notification to %s: %s", label, exc
                )


def _send_discord_notification_sync(ach_def):
    """Synchronous single-achievement delivery (used by tests and helpers)."""
    _send_discord_notification_batch([{"ach": ach_def, "origin": _session_chat_id()}])


# ── State management (thread-safe, cached in memory) ────────────────────

# Guards the in-memory _state dict. RLock (not Lock) because the hooks are
# wrapped with _synchronized (see register()) while _save_state/_load_state
# re-acquire it internally — the same thread must be able to re-enter.
_state_lock = threading.RLock()
_state = None  # loaded lazily
_last_save_ts = 0.0  # debounce: don't write state.json more than once per 2s


def _save_state(force=False):
    """Persist state to disk.

    Debounced: with post_tool_call firing on every tool execution, writing
    the JSON file each time would be wasteful. Saves at most once per 2s
    unless force=True (used by post_llm_call / on_session_end).
    """
    global _last_save_ts
    if _state is None:
        return  # nothing loaded yet (e.g. finalize before first hook)
    with _state_lock:
        now = time.monotonic()
        if not force and _last_save_ts and (now - _last_save_ts) < 2.0:
            return
        try:
            os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
            def _convert(v):
                return sorted(v) if isinstance(v, set) else v
            state_copy = json.loads(json.dumps(_state, default=_convert))
            state_copy["last_updated"] = datetime.now(UTC).isoformat()
            # Keep a rolling backup so a crash mid-write never loses progress
            try:
                if os.path.exists(_STATE_PATH):
                    shutil.copy2(_STATE_PATH, _STATE_BAK_PATH)
            except OSError:
                pass
            # Atomic write: temp file + os.replace so a crash or concurrent
            # reader never observes a truncated/partial state.json (the old
            # open(path, "w") truncated in place first, then wrote — a kill
            # between the two left a torn file that only the backup could fix).
            tmp_path = _STATE_PATH + ".tmp"
            try:
                with open(tmp_path, "w") as f:
                    json.dump(state_copy, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, _STATE_PATH)
            except Exception:
                # Failed mid-write: never leave a half-written temp behind
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
            _last_save_ts = now
        except Exception as exc:  # noqa: BLE001 — state save must never crash hooks
            import logging
            logging.getLogger(__name__).warning("Failed to save state: %s", exc)


def _load_state():
    global _state
    if _state is not None:
        return _state
    with _state_lock:
        if _state is not None:
            return _state
        if os.path.exists(_STATE_PATH):
            try:
                with open(_STATE_PATH) as f:
                    _state = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Corrupted state — try the rolling backup before resetting
                _state = None
                if os.path.exists(_STATE_BAK_PATH):
                    try:
                        with open(_STATE_BAK_PATH) as f:
                            _state = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        _state = None
        if _state is None:
            _state = _new_state()
        _normalize_state()
        _init_achievements()
        return _state


def _new_state():
    return {
        "achievements": {},
        "stats": {
            "total_turns": 0,
            "tools_used": {},
            "platforms": set(),
            "models_used": set(),
            "providers_used": set(),
            "slash_commands_used": set(),
            "skills_installed": 0,
            "skills_created": 0,
            "plugins_enabled": 0,
            "profiles_created": 0,
            "cron_jobs_created": 0,
            "mcp_servers_connected": 0,
            "config_changes": 0,
            "yolo_tasks": 0,
            "session_resumes": 0,
            "hooks_used": set(),
            "users_seen": set(),
            "subagents_spawned": 0,
            "subagents_failed": 0,
            "approvals_always": 0,
            "approvals_denied": 0,
            "approval_requests": 0,
            "api_errors": 0,
            "concurrent_subagents": 0,
            "max_concurrent_subagents": 0,
            "session_resets": 0,
            "last_active_date": None,
            "current_streak": 0,
            "longest_streak": 0,
            "total_sessions": 0,
            "last_session_id": None,
            "conversations_started": 0,
            "peak_tools_per_response": 0,
            "peak_terminal_output_bytes": 0,
            "peak_tool_result_bytes": 0,
            "env_types": set(),
            "longest_response_words": 0,
            "truncated_responses": 0,
            "longest_subagent_ms": 0,
            "tool_interrupts": 0,
            "tool_blocks": 0,
            "max_retry_depth": 0,
            "approvals_gateway": 0,
            "approved_patterns": set(),
            "primary_users": {},
            # Live per-session tracking (reset whenever session_id changes)
            "active_session": {
                "id": None,
                "calls": 0,
                "tool_names": set(),
                "fast_streak": 0,
            },
        },
        "newly_unlocked": [],
        "locale": "en",
        "last_updated": None,
    }


def _normalize_state():
    """Convert list fields back to sets for internal use."""
    stats = _state.setdefault("stats", {})
    for key in ("platforms", "models_used", "providers_used", "slash_commands_used", "hooks_used", "users_seen", "env_types", "approved_patterns"):
        v = stats.get(key)
        if isinstance(v, set):
            continue
        if isinstance(v, (list, tuple)):
            stats[key] = set(v)
        else:
            # Corrupted/legacy scalar (e.g. int 0) — reset to empty set
            stats[key] = set()
    # Active session's tool_names may have been persisted as a list
    active = stats.get("active_session")
    if not isinstance(active, dict):
        stats["active_session"] = {"id": None, "calls": 0, "tool_names": set(), "fast_streak": 0}
        return
    tn = active.get("tool_names")
    if isinstance(tn, (list, tuple)):
        active["tool_names"] = set(tn)
    elif not isinstance(tn, set):
        active["tool_names"] = set()
    # Default missing scalar keys even when tool_names was already a set
    for k in ("id", "calls", "fast_streak"):
        if k not in active:
            active[k] = None if k == "id" else 0


def _init_achievements():
    # Prune stale entries for achievements that no longer exist (removed
    # or renamed across versions) — keeps state.json clean.
    for aid in list(_state.setdefault("achievements", {})):
        if aid not in ACHIEVEMENT_DEFS:
            del _state["achievements"][aid]
    for aid in ACHIEVEMENT_DEFS:
        if aid not in _state["achievements"]:
            _state["achievements"][aid] = {"unlocked": False}


# ── i18n / Locale ────────────────────────────────────────────────────────

# Locale lookup order: (1) git-checkout / live-plugin dir under HERMES_HOME
# (the normal install), (2) data-files shipped inside the wheel. Wheel
# data-files install to <sys.prefix>/achievements/ (setuptools data-files
# are prefix-relative and FLATTENED — locales/*.json lands directly in
# achievements/, no locales subdir). The old guess of
# "<site-packages>/achievements/locales" never existed, which silently
# broke i18n for pip-installed copies (English-only fallback); fixed in
# v2.18.5. Falls back to English-only when neither exists (defensive;
# _t then returns raw keys).
_LOCALES_DIR = os.path.join(_HERMES_HOME, "plugins", "achievements", "locales")
_WHEEL_DATA_DIR = os.path.join(sys.prefix, "achievements")
_locales_cache = {}


def _load_locales():
    """Load all locale files into cache."""
    global _locales_cache
    if _locales_cache:
        return _locales_cache
    _locales_cache = {}
    for candidate in (_LOCALES_DIR, _WHEEL_DATA_DIR):
        try:
            for fname in sorted(os.listdir(candidate)):
                if fname.endswith(".json"):
                    lang = fname[:-5]
                    with open(os.path.join(candidate, fname), encoding="utf-8") as f:
                        _locales_cache[lang] = json.load(f)
            if _locales_cache:
                break
        except OSError:
            continue
    if "en" not in _locales_cache:
        _locales_cache["en"] = {}
    return _locales_cache


def _t(key, locale=None, **kwargs):
    """Translate a dot-separated key using the active locale.

    Keys look like:  achievement.first_steps.name  |  ui.stats_title  |  group.getting_started
    Falls back to English, then to the raw key.
    If kwargs are given, formats the string with .format(**kwargs).
    """
    if locale is None:
        state = _load_state()
        locale = state.get("locale", "en")
    if not isinstance(locale, str) or locale not in ("en", "es", "fr", "pt"):
        locale = "en"

    locales = _load_locales()
    parts = key.split(".")
    val = None
    for lang in (locale, "en"):
        data = locales.get(lang, {})
        v = data
        for part in parts:
            if isinstance(v, dict):
                v = v.get(part)
            else:
                v = None
                break
        if isinstance(v, str):
            val = v
            break
    if val is None:
        return key
    if kwargs:
        try:
            val = val.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return val


# ── Per-turn tracking ────────────────────────────────────────────────────

# (per-turn tracking state lives in stats.active_session; no module globals)


# ── Achievement Definitions (139 total) ──────────────────────────────────

ACHIEVEMENT_DEFS = {
    # ═══════════════════════════════════════════════════════════════════════
    # 🚀 GETTING STARTED  (16)
    # ═══════════════════════════════════════════════════════════════════════
    "first_steps": {
        "id": "first_steps", "name": "First Steps", "emoji": "👣",
        "description": "Send your first message to Hermes",
        "rarity": "common", "group": "Getting Started",
    },
    "config_tinkerer": {
        "id": "config_tinkerer", "name": "Config Tinkerer", "emoji": "🔧",
        "description": "Change a Hermes configuration setting",
        "rarity": "common", "group": "Getting Started",
    },
    "show_and_tell": {
        "id": "show_and_tell", "name": "Show and Tell", "emoji": "🖼️",
        "description": "Send an image or media attachment to Hermes",
        "rarity": "common", "group": "Getting Started",
    },
    "doctor_visit": {
        "id": "doctor_visit", "name": "Clean Bill of Health", "emoji": "🏥",
        "description": "Run `hermes doctor` to check system health",
        "rarity": "common", "group": "Getting Started",
    },
    "model_hopper": {
        "id": "model_hopper", "name": "Model Hopper", "emoji": "🎭",
        "description": "Switch to a different AI model",
        "rarity": "common", "group": "Getting Started",
    },
    "chatty": {
        "id": "chatty", "name": "Chatty", "emoji": "🗣️",
        "description": "Send 25 messages to Hermes",
        "rarity": "common", "group": "Getting Started",
    },
    "night_owl": {
        "id": "night_owl", "name": "Night Owl", "emoji": "🌙",
        "description": "Use Hermes after midnight (local time)",
        "rarity": "uncommon", "group": "Getting Started",
    },
    "slash_commander": {
        "id": "slash_commander", "name": "Slash Commander", "emoji": "📋",
        "description": "Use 3 different slash commands",
        "rarity": "common", "group": "Getting Started",
    },
    "persistent": {
        "id": "persistent", "name": "Persistent", "emoji": "🔄",
        "description": "Send messages across 3 different sessions",
        "rarity": "common", "group": "Getting Started",
    },
    "fresh_start": {
        "id": "fresh_start", "name": "Fresh Start", "emoji": "🌱",
        "description": "Start a fresh session with /new or /reset",
        "rarity": "common", "group": "Getting Started",
        "secret": True,
    },
    "icebreaker": {
        "id": "icebreaker", "name": "Icebreaker", "emoji": "🧊",
        "description": "Start your first conversation",
        "rarity": "uncommon", "group": "Getting Started",
    },
    "conversation_habit": {
        "id": "conversation_habit", "name": "Conversation Habit", "emoji": "💬",
        "description": "Start 10 conversations",
        "rarity": "rare", "group": "Getting Started",
    },
    "serial_starter": {
        "id": "serial_starter", "name": "Serial Starter", "emoji": "🔥",
        "description": "Start 50 conversations",
        "rarity": "epic", "group": "Getting Started",
    },
    "conversation_colossus": {
        "id": "conversation_colossus", "name": "Conversation Colossus", "emoji": "🗼",
        "description": "Start 100 conversations",
        "rarity": "legendary", "group": "Getting Started",
    },
    "cautious": {
        "id": "cautious", "name": "Cautious", "emoji": "🛡️",
        "description": "Deny an approval request",
        "rarity": "uncommon", "group": "Getting Started",
        "secret": True,
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 🛠️ TOOLS & SKILLS  (28)
    # ═══════════════════════════════════════════════════════════════════════
    "ghost_in_shell": {
        "id": "ghost_in_shell", "name": "Ghost in the Shell", "emoji": "👻",
        "description": "Run your first terminal command through Hermes",
        "rarity": "common", "group": "Tools & Skills",
    },
    "web_walker": {
        "id": "web_walker", "name": "Web Walker", "emoji": "🌐",
        "description": "Search the web using Hermes",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "visionary": {
        "id": "visionary", "name": "Visionary", "emoji": "👁️",
        "description": "Analyze an image with Hermes",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "code_wizard": {
        "id": "code_wizard", "name": "Code Wizard", "emoji": "🧪",
        "description": "Execute 10 code blocks with execute_code",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "skill_finder": {
        "id": "skill_finder", "name": "Skill Collector", "emoji": "🧠",
        "description": "Install a skill from the hub",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "skill_author": {
        "id": "skill_author", "name": "Skill Author", "emoji": "✍️",
        "description": "Create your own custom Hermes skill",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "memory_holder": {
        "id": "memory_holder", "name": "Memory Keeper", "emoji": "📖",
        "description": "Save a fact to persistent memory",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "tool_collector": {
        "id": "tool_collector", "name": "Jack of All Trades", "emoji": "🛠️",
        "description": "Use 5 different Hermes tool types in a single session",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "terminal_jockey": {
        "id": "terminal_jockey", "name": "Terminal Jockey", "emoji": "🖥️",
        "description": "Run 25 terminal commands",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "shell_master": {
        "id": "shell_master", "name": "Shell Master", "emoji": "🖥️",
        "description": "Run 100 terminal commands",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "deep_diver": {
        "id": "deep_diver", "name": "Deep Diver", "emoji": "🔍",
        "description": "Perform 25 web searches",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "code_slinger": {
        "id": "code_slinger", "name": "Code Slinger", "emoji": "💻",
        "description": "Execute 50 code blocks",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "code_architect": {
        "id": "code_architect", "name": "Code Architect", "emoji": "💻",
        "description": "Execute 100 code blocks",
        "rarity": "epic", "group": "Tools & Skills",
    },
    "skill_apprentice": {
        "id": "skill_apprentice", "name": "Skill Apprentice", "emoji": "🧠",
        "description": "Install 5 skills",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "skill_master": {
        "id": "skill_master", "name": "Skill Master", "emoji": "🧠",
        "description": "Install 15 skills",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "skill_artisan": {
        "id": "skill_artisan", "name": "Skill Artisan", "emoji": "✍️",
        "description": "Create 5 skills",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "skill_virtuoso": {
        "id": "skill_virtuoso", "name": "Skill Virtuoso", "emoji": "✍️",
        "description": "Create 15 skills",
        "rarity": "epic", "group": "Tools & Skills",
    },
    "memory_archivist": {
        "id": "memory_archivist", "name": "Memory Archivist", "emoji": "📖📖",
        "description": "Save 25 facts to memory",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "memory_librarian": {
        "id": "memory_librarian", "name": "Memory Librarian", "emoji": "📖📖📖",
        "description": "Save 100 facts to memory",
        "rarity": "epic", "group": "Tools & Skills",
    },
    "cron_master": {
        "id": "cron_master", "name": "Cron Master", "emoji": "⏰",
        "description": "Have 5 active cron jobs",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "cron_overlord": {
        "id": "cron_overlord", "name": "Cron Overlord", "emoji": "⏰",
        "description": "Have 15 active cron jobs",
        "rarity": "epic", "group": "Tools & Skills",
    },
    "tool_hoarder": {
        "id": "tool_hoarder", "name": "Tool Hoarder", "emoji": "🛠️",
        "description": "Use 10 different Hermes tool types cumulatively",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "complete_toolset": {
        "id": "complete_toolset", "name": "Complete Toolset", "emoji": "🛠️🛠️",
        "description": "Use every available Hermes tool type at least once",
        "rarity": "epic", "group": "Tools & Skills",
    },
    "file_whisperer": {
        "id": "file_whisperer", "name": "File Whisperer", "emoji": "📁",
        "description": "Read or write 25 files",
        "rarity": "uncommon", "group": "Tools & Skills",
    },
    "file_artisan": {
        "id": "file_artisan", "name": "File Artisan", "emoji": "📁",
        "description": "Read or write 100 files",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "mcp_networker": {
        "id": "mcp_networker", "name": "MCP Networker", "emoji": "🔌",
        "description": "Connect 3 MCP servers",
        "rarity": "rare", "group": "Tools & Skills",
    },
    "army_commander": {
        "id": "army_commander", "name": "Army Commander", "emoji": "👥",
        "description": "Spawn 25 subagents with delegate_task",
        "rarity": "epic", "group": "Tools & Skills",
    },
    "session_detective": {
        "id": "session_detective", "name": "Session Detective", "emoji": "🔍",
        "description": "Search past sessions 10 times",
        "rarity": "uncommon", "group": "Tools & Skills",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # ⚡ POWER USER  (44)
    # ═══════════════════════════════════════════════════════════════════════
    "cron_commander": {
        "id": "cron_commander", "name": "Cron Commander", "emoji": "⏰",
        "description": "Schedule your first cron job",
        "rarity": "rare", "group": "Power User",
    },
    "mcp_master": {
        "id": "mcp_master", "name": "MCP Master", "emoji": "🔌",
        "description": "Add an MCP server connection",
        "rarity": "rare", "group": "Power User",
    },
    "agent_swarm": {
        "id": "agent_swarm", "name": "Agent Swarm", "emoji": "👥",
        "description": "Spawn a subagent with delegate_task",
        "rarity": "rare", "group": "Power User",
    },
    "yolo_mode": {
        "id": "yolo_mode", "name": "YOLO Mode", "emoji": "🔐",
        "description": "Run with --yolo flag or disable approval prompts",
        "rarity": "epic", "group": "Power User",
    },
    "session_sage": {
        "id": "session_sage", "name": "Session Sage", "emoji": "🔄",
        "description": "Resume a past session with --continue or /resume",
        "rarity": "uncommon", "group": "Power User",
    },
    "profile_juggler": {
        "id": "profile_juggler", "name": "Profile Juggler", "emoji": "👤",
        "description": "Create a named Hermes profile",
        "rarity": "rare", "group": "Power User",
    },
    "yolo_champion": {
        "id": "yolo_champion", "name": "YOLO Champion", "emoji": "🔐",
        "description": "Complete 25 tasks without approval prompts",
        "rarity": "epic", "group": "Power User",
    },
    "profile_collector": {
        "id": "profile_collector", "name": "Profile Collector", "emoji": "👤",
        "description": "Create 5 Hermes profiles",
        "rarity": "rare", "group": "Power User",
    },
    "marathon_session": {
        "id": "marathon_session", "name": "Marathon Session", "emoji": "🤖",
        "description": "Reach 200 tool calls in a single session",
        "rarity": "legendary", "group": "Power User",
    },
    "gateway_networker": {
        "id": "gateway_networker", "name": "Gateway Networker", "emoji": "🌉",
        "description": "Connect to 3 different messaging platforms",
        "rarity": "epic", "group": "Power User",
    },
    "plugin_developer": {
        "id": "plugin_developer", "name": "Plugin Developer", "emoji": "🧩",
        "description": "Create your own Hermes plugin",
        "rarity": "legendary", "group": "Power User",
    },
    "plugin_pack": {
        "id": "plugin_pack", "name": "Plugin Pack", "emoji": "🧩",
        "description": "Have 5 plugins enabled",
        "rarity": "epic", "group": "Power User",
    },
    "chain_reaction": {
        "id": "chain_reaction", "name": "Chain Reaction", "emoji": "⛓️",
        "description": "Chain 2 cron jobs together with context_from",
        "rarity": "rare", "group": "Power User",
    },
    "multi_model": {
        "id": "multi_model", "name": "Multi-Model", "emoji": "🎭",
        "description": "Use 5 different AI models",
        "rarity": "rare", "group": "Power User",
    },
    "model_collector": {
        "id": "model_collector", "name": "Model Collector", "emoji": "🎭",
        "description": "Use 10 different AI models",
        "rarity": "epic", "group": "Power User",
    },
    "provider_hopper": {
        "id": "provider_hopper", "name": "Provider Hopper", "emoji": "🔄",
        "description": "Use 2 different AI providers",
        "rarity": "common", "group": "Getting Started",
    },
    "provider_collector": {
        "id": "provider_collector", "name": "Provider Collector", "emoji": "🔄",
        "description": "Use 5 different AI providers",
        "rarity": "rare", "group": "Power User",
    },
    "deep_dive": {
        "id": "deep_dive", "name": "Deep Dive", "emoji": "🤿",
        "description": "Let Hermes work 10 steps in a single turn",
        "rarity": "uncommon", "group": "Expert",
    },
    "context_colossus": {
        "id": "context_colossus", "name": "Context Colossus", "emoji": "🏛️",
        "description": "Make one API request with 100+ messages in context",
        "rarity": "epic", "group": "Expert",
    },
    "novelist": {
        "id": "novelist", "name": "Novelist", "emoji": "📖",
        "description": "Send a single message of 1500+ words",
        "rarity": "rare", "group": "Expert",
    },
    "context_monster": {
        "id": "context_monster", "name": "Context Monster", "emoji": "🧠",
        "description": "Send one API request with 200K+ input tokens",
        "rarity": "epic", "group": "Expert",
    },
    "token_tsunami": {
        "id": "token_tsunami", "name": "Token Tsunami", "emoji": "🌊",
        "description": "Send one API request with 500K+ input tokens",
        "rarity": "legendary", "group": "Expert",
    },
    "big_haul": {
        "id": "big_haul", "name": "Big Haul", "emoji": "📦",
        "description": "Receive a 1MB+ result from a single tool call",
        "rarity": "rare", "group": "Expert",
    },
    "colossal_result": {
        "id": "colossal_result", "name": "Colossal Result", "emoji": "🗄️",
        "description": "Receive a 10MB+ result from a single tool call",
        "rarity": "epic", "group": "Expert",
    },
    "token_wall": {
        "id": "token_wall", "name": "Token Wall", "emoji": "🛑",
        "description": "Hit the model's output token limit 25 times (finish_reason=length)",
        "rarity": "rare", "group": "Expert",
    },
    "marathon": {
        "id": "marathon", "name": "Marathon", "emoji": "🏃",
        "description": "Run a subagent that takes 60+ minutes",
        "rarity": "epic", "group": "Expert",
    },
    "workflow_builder": {
        "id": "workflow_builder", "name": "Workflow Builder", "emoji": "🏗️",
        "description": "Use 8 different tool types in a single session",
        "rarity": "rare", "group": "Power User",
    },
    "session_surfer": {
        "id": "session_surfer", "name": "Session Surfer", "emoji": "🏄",
        "description": "Resume 10 different sessions",
        "rarity": "uncommon", "group": "Power User",
    },
    "quick_draw": {
        "id": "quick_draw", "name": "Quick Draw", "emoji": "⚡",
        "description": "Complete 5 tasks with rapid turnaround",
        "rarity": "rare", "group": "Power User",
        "secret": True,
    },
    "tool_diversity": {
        "id": "tool_diversity", "name": "Tool Diversity", "emoji": "🎯",
        "description": "Use every available Hermes tool category",
        "rarity": "epic", "group": "Power User",
    },
    "parallel_master": {
        "id": "parallel_master", "name": "Parallel Master", "emoji": "⚡⚡",
        "description": "Run 3 subagents in parallel with a single delegate_task",
        "rarity": "rare", "group": "Power User",
    },
    "double_time": {
        "id": "double_time", "name": "Double Time", "emoji": "🤹",
        "description": "Emit 2 tool calls in a single response",
        "rarity": "uncommon", "group": "Power User",
    },
    "batch_artist": {
        "id": "batch_artist", "name": "Batch Artist", "emoji": "🎪",
        "description": "Emit 5 tool calls in a single response",
        "rarity": "rare", "group": "Power User",
    },
    "parallel_barrage": {
        "id": "parallel_barrage", "name": "Parallel Barrage", "emoji": "💥",
        "description": "Emit 10 tool calls in a single response",
        "rarity": "epic", "group": "Power User",
    },
    "tool_torrent": {
        "id": "tool_torrent", "name": "Tool Torrent", "emoji": "🧰",
        "description": "Emit 20 tool calls in a single response",
        "rarity": "legendary", "group": "Power User",
    },
    "command_center": {
        "id": "command_center", "name": "Command Center", "emoji": "🎚️",
        "description": "Use 10 different slash commands",
        "rarity": "rare", "group": "Power User",
    },
    "command_general": {
        "id": "command_general", "name": "Command General", "emoji": "🎖️",
        "description": "Use 25 different slash commands",
        "rarity": "epic", "group": "Power User",
    },
    "conductor": {
        "id": "conductor", "name": "Conductor", "emoji": "🎻",
        "description": "Run 3 subagents simultaneously (peak concurrency)",
        "rarity": "rare", "group": "Power User",
    },
    "orchestrator": {
        "id": "orchestrator", "name": "Orchestrator", "emoji": "🎼",
        "description": "Use an orchestrator-role subagent",
        "rarity": "rare", "group": "Power User",
        "secret": True,
    },
    "trust_fall": {
        "id": "trust_fall", "name": "Trust Fall", "emoji": "🪂",
        "description": "Approve a command permanently with 'always'",
        "rarity": "rare", "group": "Power User",
        "secret": True,
    },
    "visual_storyteller": {
        "id": "visual_storyteller", "name": "Visual Storyteller", "emoji": "🎬",
        "description": "Send 25 images or media attachments",
        "rarity": "rare", "group": "Power User",
    },
    "deep_context": {
        "id": "deep_context", "name": "Deep Context", "emoji": "🌊",
        "description": "Make one API request with 50+ messages in context",
        "rarity": "uncommon", "group": "Power User",
    },
    "wordsmith": {
        "id": "wordsmith", "name": "Wordsmith", "emoji": "✍️",
        "description": "Send a single message of 300+ words",
        "rarity": "uncommon", "group": "Power User",
    },
    "local_first": {
        "id": "local_first", "name": "Local First", "emoji": "🏠",
        "description": "Run Hermes against a local/self-hosted model endpoint",
        "rarity": "uncommon", "group": "Power User",
    },
    "self_hosted": {
        "id": "self_hosted", "name": "Self-Hosted", "emoji": "🖥️",
        "description": "Make 25 API requests to local/self-hosted endpoints",
        "rarity": "rare", "group": "Power User",
    },
    "verbose_output": {
        "id": "verbose_output", "name": "Verbose Output", "emoji": "💦",
        "description": "Produce 100KB+ of output from a single terminal command",
        "rarity": "uncommon", "group": "Power User",
    },
    "data_flood": {
        "id": "data_flood", "name": "Data Flood", "emoji": "🌋",
        "description": "Produce 1MB+ of output from a single terminal command",
        "rarity": "rare", "group": "Power User",
    },
    "multi_env": {
        "id": "multi_env", "name": "Multi-Environment", "emoji": "🏝️",
        "description": "Run terminal commands in 2 different execution environments",
        "rarity": "uncommon", "group": "Power User",
    },
    "omnipresent": {
        "id": "omnipresent", "name": "Omnipresent", "emoji": "🌌",
        "description": "Run terminal commands in 5 different execution environments",
        "rarity": "epic", "group": "Power User",
    },
    "ghost_command": {
        "id": "ghost_command", "name": "Ghost Command", "emoji": "🚫",
        "description": "Hit exit code 127 (command not found) on a terminal command",
        "rarity": "rare", "group": "Power User",
        "secret": True,
    },
    "essayist": {
        "id": "essayist", "name": "Essayist", "emoji": "🎙️",
        "description": "Receive a 1000+ word response from the model",
        "rarity": "uncommon", "group": "Power User",
    },
    "novel_author": {
        "id": "novel_author", "name": "Novel Author", "emoji": "📜",
        "description": "Receive a 5000+ word response from the model",
        "rarity": "rare", "group": "Power User",
    },
    "cut_short": {
        "id": "cut_short", "name": "Cut Short", "emoji": "✂️",
        "description": "Hit the model's output token limit (finish_reason=length)",
        "rarity": "uncommon", "group": "Power User",
    },
    "slow_thinker": {
        "id": "slow_thinker", "name": "Slow Thinker", "emoji": "🐢",
        "description": "Run a subagent that takes 10+ minutes",
        "rarity": "rare", "group": "Power User",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 👑 EXPERT  (40)
    # ═══════════════════════════════════════════════════════════════════════
    "the_90_turn_club": {
        "id": "the_90_turn_club", "name": "The 90-Turn Club", "emoji": "🤖",
        "description": "Reach 90 tool calls in a single session (default max_turns)",
        "rarity": "epic", "group": "Expert",
    },
    "cross_platform": {
        "id": "cross_platform", "name": "Cross-Platform Operative", "emoji": "📡",
        "description": "Chat with Hermes from 2+ different platforms",
        "rarity": "epic", "group": "Expert",
    },
    "gateway_guru": {
        "id": "gateway_guru", "name": "Gateway Guru", "emoji": "🌉",
        "description": "Connect Hermes to a messaging platform gateway",
        "rarity": "rare", "group": "Expert",
    },
    "plugin_power": {
        "id": "plugin_power", "name": "Plugin Power", "emoji": "🧩",
        "description": "Install and enable a Hermes plugin",
        "rarity": "rare", "group": "Expert",
    },
    "cross_platform_veteran": {
        "id": "cross_platform_veteran", "name": "Cross-Platform Veteran", "emoji": "📡📡",
        "description": "Chat with Hermes from 5+ different platforms",
        "rarity": "legendary", "group": "Expert",
    },
    "hook_master": {
        "id": "hook_master", "name": "Hook Master", "emoji": "🪝",
        "description": "Create a plugin using 3+ different hook types",
        "rarity": "legendary", "group": "Expert",
    },
    "config_guru": {
        "id": "config_guru", "name": "Config Guru", "emoji": "🔧",
        "description": "Modify 15 different configuration settings",
        "rarity": "rare", "group": "Expert",
    },
    "cli_champion": {
        "id": "cli_champion", "name": "CLI Champion", "emoji": "📈",
        "description": "Execute 500 terminal commands",
        "rarity": "legendary", "group": "Expert",
    },
    "mcp_wizard": {
        "id": "mcp_wizard", "name": "MCP Wizard", "emoji": "🔌",
        "description": "Write a custom MCP server configuration",
        "rarity": "epic", "group": "Expert",
    },
    "precision_scheduler": {
        "id": "precision_scheduler", "name": "Precision Scheduler", "emoji": "🎯",
        "description": "Schedule a one-shot cron job for a specific time",
        "rarity": "rare", "group": "Expert",
    },
    "ultra_marathon": {
        "id": "ultra_marathon", "name": "Ultra Marathon", "emoji": "💪",
        "description": "Reach 150 tool calls in a single session",
        "rarity": "legendary", "group": "Expert",
    },
    "multi_lingual": {
        "id": "multi_lingual", "name": "Multi-Lingual", "emoji": "🌍",
        "description": "Communicate with Hermes in a language other than English",
        "rarity": "uncommon", "group": "Expert",
    },
    "env_tuner": {
        "id": "env_tuner", "name": "Environment Tuner", "emoji": "⚙️",
        "description": "Configure custom environment variables for a cron job",
        "rarity": "rare", "group": "Expert",
    },
    "doc_diver": {
        "id": "doc_diver", "name": "Doc Diver", "emoji": "📚",
        "description": "Read the Hermes documentation",
        "rarity": "uncommon", "group": "Expert",
    },
    "complete_rare": {
        "id": "complete_rare", "name": "Rare Collector", "emoji": "🔷",
        "description": "Unlock every Rare achievement",
        "rarity": "epic", "group": "Expert",
    },
    "resilient": {
        "id": "resilient", "name": "Resilient", "emoji": "🧗",
        "description": "Complete a task after a subagent failed",
        "rarity": "epic", "group": "Expert",
        "secret": True,
    },
    "indestructible": {
        "id": "indestructible", "name": "Indestructible", "emoji": "🛡️",
        "description": "Survive 10 LLM API errors without quitting",
        "rarity": "epic", "group": "Expert",
    },
    "tenacious": {
        "id": "tenacious", "name": "Tenacious", "emoji": "🪨",
        "description": "Survive an API request that failed 2+ times in a row",
        "rarity": "rare", "group": "Expert",
    },
    "undeterred": {
        "id": "undeterred", "name": "Undeterred", "emoji": "⛰️",
        "description": "Survive an API request that failed 4+ times in a row",
        "rarity": "epic", "group": "Expert",
    },
    "under_scrutiny": {
        "id": "under_scrutiny", "name": "Under Scrutiny", "emoji": "🔍",
        "description": "Trigger 10 approval requests",
        "rarity": "rare", "group": "Expert",
    },
    "trial_and_error": {
        "id": "trial_and_error", "name": "Trial and Error", "emoji": "🔬",
        "description": "Persist through 25 tool calls that errored",
        "rarity": "rare", "group": "Expert",
    },
    "manual_override": {
        "id": "manual_override", "name": "Manual Override", "emoji": "✋",
        "description": "Interrupt a running tool call — take manual control",
        "rarity": "uncommon", "group": "Expert",
    },
    "backseat_driver": {
        "id": "backseat_driver", "name": "Backseat Driver", "emoji": "🗣️",
        "description": "Interrupt 5 tool calls while they run",
        "rarity": "rare", "group": "Expert",
    },
    "control_freak": {
        "id": "control_freak", "name": "Control Freak", "emoji": "🎛️",
        "description": "Interrupt 15 tool calls — you like to be in charge",
        "rarity": "epic", "group": "Expert",
    },
    "dead_end": {
        "id": "dead_end", "name": "Dead End", "emoji": "🚧",
        "description": "Hit a tool call blocked by policy before it ran",
        "rarity": "uncommon", "group": "Expert",
    },
    "brick_wall": {
        "id": "brick_wall", "name": "Brick Wall", "emoji": "🧱",
        "description": "Hit 10 tool calls blocked by policy",
        "rarity": "rare", "group": "Expert",
    },
    "remote_warden": {
        "id": "remote_warden", "name": "Remote Warden", "emoji": "🛰️",
        "description": "Approve a dangerous command from a chat platform",
        "rarity": "uncommon", "group": "Expert",
    },
    "long_distance_operator": {
        "id": "long_distance_operator", "name": "Long-Distance Operator", "emoji": "🚁",
        "description": "Approve 10 dangerous commands from a chat platform",
        "rarity": "rare", "group": "Expert",
    },
    "risk_explorer": {
        "id": "risk_explorer", "name": "Risk Explorer", "emoji": "🧨",
        "description": "Approve commands in 5 different danger classes",
        "rarity": "uncommon", "group": "Expert",
    },
    "danger_collector": {
        "id": "danger_collector", "name": "Danger Collector", "emoji": "⚗️",
        "description": "Approve commands in 15 different danger classes",
        "rarity": "rare", "group": "Expert",
    },
    "living_on_the_edge": {
        "id": "living_on_the_edge", "name": "Living on the Edge", "emoji": "☢️",
        "description": "Approve commands in 25 different danger classes",
        "rarity": "epic", "group": "Expert",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 🎯 MILESTONES  (19)
    # ═══════════════════════════════════════════════════════════════════════
    "early_bird": {
        "id": "early_bird", "name": "Early Bird", "emoji": "🐦",
        "description": "Use Hermes before 6 AM",
        "rarity": "uncommon", "group": "Milestones",
    },
    "century": {
        "id": "century", "name": "Century Mark", "emoji": "💯",
        "description": "Accumulate 100+ messages across all sessions",
        "rarity": "epic", "group": "Milestones",
    },
    "completionist": {
        "id": "completionist", "name": "Completionist", "emoji": "🏆",
        "description": "Unlock every other achievement",
        "rarity": "legendary", "group": "Milestones",
    },
    "talkative": {
        "id": "talkative", "name": "Talkative", "emoji": "💬",
        "description": "Send 500 messages total",
        "rarity": "epic", "group": "Milestones",
    },
    "legendary_chatter": {
        "id": "legendary_chatter", "name": "Legendary Chatter", "emoji": "💬💬",
        "description": "Send 1,000 messages total",
        "rarity": "legendary", "group": "Milestones",
    },
    "tool_fan": {
        "id": "tool_fan", "name": "Tool Fan", "emoji": "🔧",
        "description": "Accumulate 100 total tool calls",
        "rarity": "uncommon", "group": "Milestones",
    },
    "tool_addict": {
        "id": "tool_addict", "name": "Tool Addict", "emoji": "🔧",
        "description": "Accumulate 500 total tool calls",
        "rarity": "rare", "group": "Milestones",
    },
    "tool_obsessed": {
        "id": "tool_obsessed", "name": "Tool Obsessed", "emoji": "🔧",
        "description": "Accumulate 1,000 total tool calls",
        "rarity": "epic", "group": "Milestones",
    },
    "week_warrior": {
        "id": "week_warrior", "name": "Week Warrior", "emoji": "📅",
        "description": "Use Hermes 7 days in a row",
        "rarity": "rare", "group": "Milestones",
    },
    "monthly_master": {
        "id": "monthly_master", "name": "Monthly Master", "emoji": "📅📅",
        "description": "Use Hermes 30 days in a row",
        "rarity": "legendary", "group": "Milestones",
    },
    "power_session": {
        "id": "power_session", "name": "Power Session", "emoji": "💪",
        "description": "Make 50 tool calls in a single session",
        "rarity": "rare", "group": "Milestones",
    },
    "token_tyro": {
        "id": "token_tyro", "name": "Token Tyro", "emoji": "💧",
        "description": "Consume 100,000 tokens across all sessions",
        "rarity": "uncommon", "group": "Milestones",
    },
    "token_wizard": {
        "id": "token_wizard", "name": "Token Wizard", "emoji": "🧙",
        "description": "Consume 1,000,000 tokens across all sessions",
        "rarity": "epic", "group": "Milestones",
    },
    "token_whale": {
        "id": "token_whale", "name": "Token Whale", "emoji": "🐋",
        "description": "Consume 10,000,000 tokens across all sessions",
        "rarity": "legendary", "group": "Milestones",
    },
    "speed_demon": {
        "id": "speed_demon", "name": "Speed Demon", "emoji": "⚡",
        "description": "Get 25 API responses in under 2 seconds",
        "rarity": "rare", "group": "Milestones",
    },
    "complete_epic": {
        "id": "complete_epic", "name": "Epic Collector", "emoji": "🟣",
        "description": "Unlock every Epic achievement",
        "rarity": "legendary", "group": "Milestones",
    },
    "complete_getting_started": {
        "id": "complete_getting_started", "name": "Getting Started Complete", "emoji": "🚀",
        "description": "Unlock every Getting Started achievement",
        "rarity": "epic", "group": "Milestones",
    },
    "complete_tools": {
        "id": "complete_tools", "name": "Tools Complete", "emoji": "🛠️",
        "description": "Unlock every Tools & Skills achievement",
        "rarity": "legendary", "group": "Milestones",
    },
    "complete_power_user": {
        "id": "complete_power_user", "name": "Power User Complete", "emoji": "⚡",
        "description": "Unlock every Power User achievement",
        "rarity": "legendary", "group": "Milestones",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # 🤝 COMMUNITY  (6)
    # ═══════════════════════════════════════════════════════════════════════
    "release_reader": {
        "id": "release_reader", "name": "Release Reader", "emoji": "📝",
        "description": "Read the latest Hermes release notes",
        "rarity": "uncommon", "group": "Community",
    },
    "changelog_checker": {
        "id": "changelog_checker", "name": "Changelog Checker", "emoji": "📋",
        "description": "Read the Hermes changelog",
        "rarity": "common", "group": "Community",
    },
    "social_butterfly": {
        "id": "social_butterfly", "name": "Social Butterfly", "emoji": "🦋",
        "description": "Receive messages from 3 different users",
        "rarity": "uncommon", "group": "Community",
    },
    "party_host": {
        "id": "party_host", "name": "Party Host", "emoji": "🎉",
        "description": "Receive messages from 10 different users",
        "rarity": "rare", "group": "Community",
    },
    "first_config": {
        "id": "first_config", "name": "First Config", "emoji": "⚙️",
        "description": "View the Hermes configuration",
        "rarity": "common", "group": "Community",
    },
    "complete_community": {
        "id": "complete_community", "name": "Community Complete", "emoji": "🤝",
        "description": "Unlock every Community achievement",
        "rarity": "epic", "group": "Community",
    },
}

GROUPS = ["Getting Started", "Tools & Skills", "Power User", "Expert", "Milestones", "Community"]
GROUP_EMOJIS = {
    "Getting Started": "🚀", "Tools & Skills": "🛠️",
    "Power User": "⚡", "Expert": "👑", "Milestones": "🎯", "Community": "🤝",
}
RARITY_EMOJIS = {
    "common": "⬜", "uncommon": "🟩", "rare": "🟦",
    "epic": "🟣", "legendary": "🟡",
}
# Discord embed accent colors per rarity (decimal RGB)
_RARITY_COLORS = {
    "common": 0x9CA3AF,
    "uncommon": 0x22C55E,
    "rare": 0x3B82F6,
    "epic": 0xA855F7,
    "legendary": 0xF59E0B,
}
NON_COMPLETIONIST_IDS = [aid for aid in ACHIEVEMENT_DEFS if aid != "completionist"]

# ── Recognition thresholds ──────────────────────────────────────────────

# Achievements that are unlocked by reaching tool usage thresholds
_TOOL_THRESHOLDS = {
    "terminal": [(25, "terminal_jockey"), (100, "shell_master"), (500, "cli_champion")],
    "web_search": [(25, "deep_diver")],
    "web_extract": [(25, "deep_diver")],
    "execute_code": [(10, "code_wizard"), (50, "code_slinger"), (100, "code_architect")],
    "memory": [(25, "memory_archivist"), (100, "memory_librarian")],
    "read_file": [(25, "file_whisperer"), (100, "file_artisan")],
    "write_file": [(25, "file_whisperer"), (100, "file_artisan")],
    "patch": [(25, "file_whisperer"), (100, "file_artisan")],
    "search_files": [(25, "file_whisperer"), (100, "file_artisan")],
    "session_search": [(10, "session_detective")],
    "web_scrape": [(25, "deep_diver")],
}

# Cumulative total tool call thresholds
_TOTAL_TOOL_THRESHOLDS = [
    (100, "tool_fan"),
    (500, "tool_addict"),
    (1000, "tool_obsessed"),
]

# Total message thresholds
_MESSAGE_THRESHOLDS = [
    (25, "chatty"),
    (100, "century"),
    (500, "talkative"),
    (1000, "legendary_chatter"),
]

# Cumulative token thresholds (usage.total_tokens from post_api_request)
_TOKEN_THRESHOLDS = [
    (100_000, "token_tyro"),
    (1_000_000, "token_wizard"),
    (10_000_000, "token_whale"),
]

# Fast API response: api_duration (seconds) below this counts as "fast"
_FAST_RESPONSE_THRESHOLD_S = 2.0
_FAST_RESPONSE_COUNT = 25

# A turn with ≥ this many provider calls is a deep autonomous run.
_DEEP_DIVE_STEPS = 10

# Media-message thresholds (from pre_gateway_dispatch event media fields)
_MEDIA_THRESHOLDS = [
    (1, "show_and_tell"),
    (25, "visual_storyteller"),
]

# Per-request context depth (message_count from post_api_request): a single
# API request carrying ≥ this many messages means a long conversation
# history was sent to the model in one shot.
_DEEP_CONTEXT_MESSAGES = 50
_CONTEXT_COLOSSUS_MESSAGES = 100

# Single-message verbosity (word count of user_message from post_llm_call)
_WORDSMITH_WORDS = 300
_NOVELIST_WORDS = 1500

# Single-request input-token spikes (approx_input_tokens from pre_api_request):
# a request carrying ≥ this many input tokens means a huge context window was
# loaded at once — distinct from cumulative token milestones.
_CONTEXT_MONSTER_INPUT_TOKENS = 200_000
_TOKEN_TSUNAMI_INPUT_TOKENS = 500_000

# Local/self-hosted model endpoints (base_url from pre_api_request): hosts
# that are loopback, private-range, or .local/.internal resolve locally.
_LOCAL_HOST_MARKERS = (
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
)
_LOCAL_PREFIXES = ("192.168.", "10.", "172.16.", "172.17.", "172.18.",
                   "172.19.", "172.20.", "172.21.", "172.22.", "172.23.",
                   "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
                   "172.29.", "172.30.", "172.31.", "169.254.")

# Conversation-start thresholds (is_first_turn from pre_llm_call): fires
# exactly once per fresh LLM context — a conversation that actually reached
# the model, distinct from session creation (on_session_start) and explicit
# /new or /reset (on_session_reset).
_CONVERSATION_THRESHOLDS = [
    (1, "icebreaker"),
    (10, "conversation_habit"),
    (50, "serial_starter"),
    (100, "conversation_colossus"),
]

# Tool-interrupt thresholds (status="cancelled" from post_tool_call — the
# user pressed stop while a tool was running, error_type keyboard_interrupt).
# Distinct from tool errors (execution failed) and approvals (consent prompt
# the user answered): an interrupt is the user actively taking control.
_INTERRUPT_THRESHOLDS = [
    (1, "manual_override"),
    (5, "backseat_driver"),
    (15, "control_freak"),
]

# Policy-block thresholds (status="blocked" from post_tool_call — a tool was
# denied BEFORE execution by scope/plugin/guardrail policy; error_type
# tool_scope_block / plugin_block / guardrail_block).
_BLOCK_THRESHOLDS = [
    (1, "dead_end"),
    (10, "brick_wall"),
]

# Retry-depth thresholds (retry_count from api_request_error): the number
# of consecutive failures the SAME request survived before the hook fired.
# The gateway's retry loop fires the hook on every failed attempt with the
# current depth (0 = first attempt), so depth measures sustained-outage
# resilience — distinct from api_errors (total breadth of failures, feeds
# Indestructible). Default api_max_retries=3 reaches depth 2; depth 4
# requires raising api_max_retries (an intentional resilience config).
_TENACIOUS_RETRY_DEPTH = 2
_UNDETERRED_RETRY_DEPTH = 4

# Approval-surface thresholds (surface from post_approval_response): the
# platform where the user answered an approval prompt. "gateway" means a
# chat adapter (Discord/Telegram/Slack) — approving a dangerous command
# REMOTELY is bolder than at the CLI, so surface is a real dimension
# distinct from the choice itself (always/deny feed YOLO/Cautious).
_APPROVAL_SURFACE_THRESHOLDS = [
    (1, "remote_warden"),
    (10, "long_distance_operator"),
]

# Danger-class diversity thresholds (pattern_keys from post_approval_response):
# the set of DISTINCT dangerous-command classes the user has approved (the
# gateway's ~40 patterns: rm, chmod, mkfs, dd, DROP TABLE, systemctl,
# kill -9, curl|sh, docker down, git push --force, sudo -S...). Repeated
# approvals of the same class add nothing — breadth of risk appetite is
# the dimension (Under Scrutiny only counts total prompt volume).
_APPROVAL_PATTERN_THRESHOLDS = [
    (5, "risk_explorer"),
    (15, "danger_collector"),
    (25, "living_on_the_edge"),
]

# Gateway-command thresholds (command from pre_gateway_dispatch): slash
# commands the USER types that the gateway intercepts BEFORE the LLM
# (/new, /reset, /title, /model, /achievements — 56 known commands) — they
# never reach post_llm_call, so the LLM-path slash_commander could never
# count the plugin's own command. Only commands from the platform's
# PRIMARY user count (the first non-bot user seen — the owner in every
# real deployment; other users' commands in shared channels must not
# unlock the user's achievements).
_GATEWAY_COMMAND_THRESHOLDS = [
    (10, "command_center"),
    (25, "command_general"),
]


# Single-response tool-batch thresholds (api_request_id from pre_tool_call):
# every tool call the model emitted in ONE assistant response shares the
# same api_request_id, so counting consecutive calls per id measures how
# many tools the model chose to run in parallel in a single step — a
# genuinely distinct dimension from cumulative tool counts and from
# delegate_task parallelism (Parallel Master).
_BATCH_THRESHOLDS = [
    (2, "double_time"),
    (5, "batch_artist"),
    (10, "parallel_barrage"),
    (20, "tool_torrent"),
]
_LOCAL_SUFFIXES = (".local", ".internal", ".lan", ".home.arpa")

# Number of requests to local endpoints for the Self-Hosted tier
_SELF_HOSTED_REQUESTS = 25

# Raw-output volume thresholds (output from transform_terminal_output): the
# hook receives the FULL command output BEFORE the terminal tool truncates
# it, so these measure what the model was handed vs. what the command
# actually produced — a dimension post_tool_call cannot see (it only gets
# the truncated result).
_VERBOSE_OUTPUT_BYTES = 100 * 1024        # 100 KiB
_DATA_FLOOD_BYTES = 1024 * 1024           # 1 MiB

# Tool-result size thresholds (result from transform_tool_result): the hook
# delivers the full result string for ANY tool (post_tool_call only gets
# status/error_type, never the result content) — measuring context bloat.
_BIG_HAUL_BYTES = 1024 * 1024             # 1 MiB
_COLOSSAL_RESULT_BYTES = 10 * 1024 * 1024 # 10 MiB

# Execution-environment diversity (env_type from transform_terminal_output):
# "local" | "ssh" | "docker" | "singularity" | "modal" | "daytona".
_ENV_THRESHOLDS = [
    (2, "multi_env"),
    (5, "omnipresent"),
]

# Exit-code milestone (returncode from transform_terminal_output): 127 is
# the classic "command not found" code — a distinct, recognizable signal
# that post_tool_call's status/error_type bucket cannot express.
_GHOST_COMMAND_EXIT_CODE = 127

# Model-response verbosity thresholds (assistant_response from
# post_llm_call): word count of the MODEL's own output — a mirror of the
# user-message verbosity dimension (Wordsmith/Novelist) that measures how
# much the model wrote in a single response, distinct from user input.
_ESSAYIST_WORDS = 1000
_NOVEL_AUTHOR_WORDS = 5000

# Output-cap truncation thresholds (finish_reason from post_api_request):
# finish_reason="length" means the model hit its max output tokens and was
# cut off — a real, recognizable event that usage buckets cannot express
# (the response came back, but incomplete).
_TRUNCATION_THRESHOLDS = [
    (1, "cut_short"),
    (25, "token_wall"),
]

# Subagent runtime thresholds (duration_ms from subagent_stop): how long a
# delegated child actually ran — a dimension subagent counting cannot see.
_SLOW_SUBAGENT_MS = 10 * 60 * 1000    # 10 minutes
_MARATHON_SUBAGENT_MS = 60 * 60 * 1000  # 60 minutes

# Single-session tool call thresholds
_SESSION_CALL_THRESHOLDS = [
    (50, "power_session"),
    (90, "the_90_turn_club"),
    (150, "ultra_marathon"),
    (200, "marathon_session"),
]

# Cumulative distinct tool types used ever thresholds
_TOOL_DIVERSITY_THRESHOLDS = [
    (10, "tool_hoarder"),
]

# Distinct tool types in a single session thresholds
_SESSION_TOOL_THRESHOLDS = [
    (5, "tool_collector"),
    (8, "workflow_builder"),
]

# Tool-name → achievement mappings (for first-use detection)
_TOOL_ACHIEVEMENTS = {
    "terminal": "ghost_in_shell",
    "web_search": "web_walker",
    "web_extract": "web_walker",
    "delegate_task": "agent_swarm",
    "vision_analyze": "visionary",
}

_TOOL_CATEGORIES = {
    "terminal": "terminal", "web_search": "web", "web_extract": "web",
    "browser_navigate": "browser", "browser_click": "browser",
    "browser_snapshot": "browser", "browser_type": "browser",
    "vision_analyze": "vision",
    "execute_code": "code_exec", "delegate_task": "delegation",
    "read_file": "file", "write_file": "file", "patch": "file",
    "search_files": "file", "memory": "memory", "skill_manage": "skills",
    "cronjob": "cron", "session_search": "search",
    "send_message": "messaging", "text_to_speech": "audio",
    "todo": "todo", "clarify": "interactive",
    "web_scrape": "web",
}

# All known tool type IDs for "complete_toolset"
_ALL_TOOL_TYPES = set(_TOOL_CATEGORIES.keys())

TERMINAL_PATTERNS = {
    "config_tinkerer": [re.compile(r"hermes\s+config\s+(set|edit)", re.IGNORECASE)],
    "doctor_visit": [re.compile(r"hermes\s+doctor", re.IGNORECASE)],
    "mcp_master": [re.compile(r"hermes\s+mcp\s+add", re.IGNORECASE)],
    "profile_juggler": [re.compile(r"hermes\s+profile\s+create", re.IGNORECASE)],
    "plugin_power": [re.compile(r"hermes\s+plugins\s+enable", re.IGNORECASE)],
    "session_sage": [re.compile(r"/resume|--continue", re.IGNORECASE)],
    "yolo_mode": [re.compile(r"--yolo\b", re.IGNORECASE)],
    "release_reader": [re.compile(r"hermes\s+changelog|CHANGELOG|release.notes", re.IGNORECASE)],
    "changelog_checker": [re.compile(r"hermes\s+changelog|CHANGELOG", re.IGNORECASE)],
    "mcp_wizard": [re.compile(r"hermes\s+mcp\s+(config|edit)", re.IGNORECASE)],
    "first_config": [re.compile(r"hermes\s+config\s+(get|show|list|view|cat|status)", re.IGNORECASE)],
    "env_tuner": [re.compile(r"workdir=|env_file|EnvironmentFile|hermes\s+config\s+set\s+env", re.IGNORECASE)],
    "precision_scheduler": [re.compile(r"cron.*ISO|one.?shot", re.IGNORECASE)],
    "doc_diver": [re.compile(r"hermes.*docs?|hermes.*documentation|hermes-agent.*docs", re.IGNORECASE)],
}


# ── Achievement detection helpers ───────────────────────────────────────

def _unlock(ach_id, now=None):
    """Unlock an achievement if not already. Returns True if newly unlocked."""
    if now is None:
        now = datetime.now(UTC).isoformat()
    state = _load_state()
    a = state["achievements"].get(ach_id, {})
    if a.get("unlocked"):
        return False
    state["achievements"][ach_id] = {"unlocked": True, "unlocked_at": now}
    state.setdefault("newly_unlocked", [])
    if ach_id not in state["newly_unlocked"]:
        state["newly_unlocked"].append(ach_id)
        # Keep the list bounded (most recent 20 only)
        state["newly_unlocked"] = state["newly_unlocked"][-20:]
    # Fire immediate Discord notification
    ach_def = ACHIEVEMENT_DEFS.get(ach_id)
    if ach_def:
        _send_discord_notification(ach_def)
    return True


def _set_progress(ach_id, current, target):
    """Set progress for a locked achievement."""
    state = _load_state()
    a = state["achievements"].get(ach_id, {})
    if a.get("unlocked"):
        return
    state["achievements"][ach_id] = {
        "unlocked": False,
        "progress": {"current": current, "target": target},
    }


def _check_completionist():
    """Check if all non-completionist achievements are unlocked."""
    state = _load_state()
    locked = sum(1 for aid in NON_COMPLETIONIST_IDS
                 if not state["achievements"].get(aid, {}).get("unlocked"))
    if locked == 0:
        _unlock("completionist")


def _check_group_completions():
    """Check group-completion achievements."""
    state = _load_state()
    ach_state = state["achievements"]

    # Group-completion checks
    for group, ach_id in [
        ("Getting Started", "complete_getting_started"),
        ("Tools & Skills", "complete_tools"),
        ("Power User", "complete_power_user"),
        ("Community", "complete_community"),
    ]:
        group_ids = [aid for aid, adef in ACHIEVEMENT_DEFS.items()
                     if adef.get("group") == group and aid != ach_id]
        locked = sum(1 for aid in group_ids
                     if not ach_state.get(aid, {}).get("unlocked"))
        if locked == 0:
            _unlock(ach_id)

    # Rarity-completion checks
    for rarity, ach_id in [("rare", "complete_rare"), ("epic", "complete_epic")]:
        rarity_ids = [aid for aid, adef in ACHIEVEMENT_DEFS.items()
                      if adef.get("rarity") == rarity and aid != ach_id]
        locked = sum(1 for aid in rarity_ids
                     if not ach_state.get(aid, {}).get("unlocked"))
        if locked == 0:
            _unlock(ach_id)


def _check_tool_usage_thresholds(tc_counts, now):
    """Check tiered thresholds for individual tool usage.

    Tools that feed the same achievement (e.g. web_search/web_extract/
    web_scrape → deep_diver, or the four file tools → file tiers) report
    the BEST (max) count so progress bars don't flicker between per-tool
    values. Unlock still requires one tool type to actually cross the
    threshold.
    """
    if not tc_counts:
        return
    # achievement id → best count across all tools that feed it
    ach_best = {}
    for tool_name, count in tc_counts.items():
        for _threshold, ach_id in _TOOL_THRESHOLDS.get(tool_name, []):
            ach_best[ach_id] = max(ach_best.get(ach_id, 0), count)
    # Check each achievement once (avoid duplicate checks for shared tiers)
    seen = set()
    for tool_name, count in tc_counts.items():
        for threshold, ach_id in _TOOL_THRESHOLDS.get(tool_name, []):
            if ach_id in seen:
                continue
            seen.add(ach_id)
            best_count = ach_best.get(ach_id, count)
            if best_count >= threshold:
                _unlock(ach_id, now)
            else:
                _set_progress(ach_id, best_count, threshold)

    # Cumulative total tool calls
    total = sum(tc_counts.values()) if tc_counts else 0
    for threshold, ach_id in _TOTAL_TOOL_THRESHOLDS:
        if total >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, total, threshold)


def _check_message_thresholds(total_turns, now):
    """Check total message thresholds."""
    for threshold, ach_id in _MESSAGE_THRESHOLDS:
        if total_turns >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, total_turns, threshold)


def _check_token_thresholds(total_tokens, now):
    """Check cumulative token thresholds."""
    for threshold, ach_id in _TOKEN_THRESHOLDS:
        if total_tokens >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, total_tokens, threshold)


def _check_session_thresholds(session_call_count, now):
    """Check single-session tool call thresholds."""
    for threshold, ach_id in _SESSION_CALL_THRESHOLDS:
        if session_call_count >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, session_call_count, threshold)


def _check_session_tool_thresholds(tool_names, now):
    """Check distinct-tool-types-in-a-session thresholds.

    Descriptions say "tool types" (e.g. Jack of All Trades = 5 different
    Hermes tool types in a single session) — count distinct tool names,
    NOT categories (read_file/write_file/search_files are distinct tools).
    """
    distinct = set(tool_names) if tool_names else set()
    for threshold, ach_id in _SESSION_TOOL_THRESHOLDS:
        if len(distinct) >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, len(distinct), threshold)


def _check_tool_diversity(tc_counts, now):
    """Check cumulative distinct tool type thresholds."""
    distinct_tools = len(tc_counts) if tc_counts else 0
    for threshold, ach_id in _TOOL_DIVERSITY_THRESHOLDS:
        if distinct_tools >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, distinct_tools, threshold)

    # Complete Toolset: used every known tool type at least once.
    # Superset check — unknown/plugin tools (e.g. `process`) must not
    # permanently brick the achievement by breaking exact equality.
    all_used = set(tc_counts.keys()) if tc_counts else set()
    if all_used and _ALL_TOOL_TYPES.issubset(all_used):
        _unlock("complete_toolset", now)
    elif all_used:
        _set_progress("complete_toolset",
                      len(all_used & _ALL_TOOL_TYPES), len(_ALL_TOOL_TYPES))

    # Tool Diversity: used every category
    all_cats = set()
    for tn in all_used:
        all_cats.add(_TOOL_CATEGORIES.get(tn, tn))
    all_cat_ids = set(_TOOL_CATEGORIES.values())
    if all_cats and all_cat_ids.issubset(all_cats):
        _unlock("tool_diversity", now)
    elif all_cats:
        _set_progress("tool_diversity",
                      len(all_cat_ids & all_cats), len(all_cat_ids))


def _check_streaks(stats, now):
    """Check streak-based achievements."""
    streak = stats.get("current_streak", 0)
    for threshold, ach_id in [(7, "week_warrior"), (30, "monthly_master")]:
        if streak >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, streak, threshold)


# ── Hook: post_tool_call ─────────────────────────────────────────────────
# Fires after EVERY tool execution with tool_name + full args. Primary
# per-tool detection path (replaces scanning conversation_history, which
# lacks argument details).

def _post_tool_call(**kwargs):
    """Detect tool-usage achievements from each tool execution."""
    tool_name = kwargs.get("tool_name", "")
    args = kwargs.get("args") or {}
    session_id = kwargs.get("session_id", "")
    duration_ms = kwargs.get("duration_ms", 0)

    if not tool_name:
        return

    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    # ── Cumulative per-tool usage ──────────────────────────────
    tc_counts = stats.setdefault("tools_used", {})
    tc_counts[tool_name] = tc_counts.get(tool_name, 0) + 1

    # ── Per-session tracking (reset on session change) ─────────
    active = stats.setdefault("active_session", {})
    if active.get("id") != session_id:
        stats["active_session"] = {
            "id": session_id,
            "calls": 0,
            "tool_names": set(),
            "fast_streak": 0,
        }
        active = stats["active_session"]
    active["calls"] = active.get("calls", 0) + 1
    active.setdefault("tool_names", set()).add(tool_name)

    # ── Quick Draw: 5 consecutive fast tool calls ──────────────
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        active["fast_streak"] = (
            active.get("fast_streak", 0) + 1 if duration_ms < 20000 else 0
        )
        if active["fast_streak"] >= 5:
            _unlock("quick_draw", now)

    # ── Tool-status resilience ──────────────────────────────────────
    # Status from gateway: "ok" | "cancelled" | "blocked" | "error".
    #   error    → execution failed (Trial and Error)
    #   cancelled→ user pressed stop mid-tool (keyboard_interrupt)
    #   blocked  → policy denied the tool BEFORE it ran
    #             (tool_scope_block / plugin_block / guardrail_block)
    status = kwargs.get("status", "ok")
    if status == "error":
        stats["tool_errors"] = stats.get("tool_errors", 0) + 1
        if stats["tool_errors"] >= 25:
            _unlock("trial_and_error", now)
        else:
            _set_progress("trial_and_error", stats["tool_errors"], 25)
    elif status == "cancelled":
        stats["tool_interrupts"] = stats.get("tool_interrupts", 0) + 1
        interrupt_count = stats["tool_interrupts"]
        for threshold, ach_id in _INTERRUPT_THRESHOLDS:
            if interrupt_count >= threshold:
                _unlock(ach_id, now)
            else:
                _set_progress(ach_id, interrupt_count, threshold)
    elif status == "blocked":
        stats["tool_blocks"] = stats.get("tool_blocks", 0) + 1
        block_count = stats["tool_blocks"]
        for threshold, ach_id in _BLOCK_THRESHOLDS:
            if block_count >= threshold:
                _unlock(ach_id, now)
            else:
                _set_progress(ach_id, block_count, threshold)

    # ── First-use achievements ─────────────────────────────────
    ach_id = _TOOL_ACHIEVEMENTS.get(tool_name)
    if ach_id:
        _unlock(ach_id, now)

    # ── Argument-based achievements ────────────────────────────
    _check_tool_args(tool_name, args, stats, now)

    # ── Threshold checks (cumulative + session-scoped) ─────────
    _check_tool_usage_thresholds(tc_counts, now)
    _check_tool_diversity(tc_counts, now)
    _check_session_thresholds(active.get("calls", 0), now)
    _check_session_tool_thresholds(active.get("tool_names", set()), now)

    # ── Group / rarity completions ─────────────────────────────
    _check_group_completions()
    _check_completionist()

    # Debounced save (at most every 2s — tool calls can be frequent)
    _save_state()


def _check_tool_args(tool_name, args, stats, now):
    """Argument-based achievement detection (cronjob, delegate_task, ...)."""
    if not isinstance(args, dict):
        args = {}

    if tool_name == "cronjob":
        action = str(args.get("action", ""))
        if action == "create":
            stats["cron_jobs_created"] = stats.get("cron_jobs_created", 0) + 1
            _unlock("cron_commander", now)
            # Precision Scheduler: one-shot ISO schedule or repeat='once'
            sched = args.get("schedule", "")
            repeat = args.get("repeat")
            if (
                isinstance(sched, str) and re.search(r"\d{4}-\d{2}-\d{2}[T ]", sched)
            ) or repeat in ("once", 1, True):
                _unlock("precision_scheduler", now)
            # Environment Tuner: custom env/workdir for the job
            if args.get("workdir") or args.get("env_file"):
                _unlock("env_tuner", now)
        # Chain Reaction: cron job chained via context_from
        if args.get("context_from"):
            _unlock("chain_reaction", now)

    elif tool_name == "delegate_task":
        tasks = args.get("tasks")
        n = len(tasks) if isinstance(tasks, (list, tuple)) else 1
        stats["parallel_spawns"] = stats.get("parallel_spawns", 0) + max(1, n)
        if isinstance(tasks, (list, tuple)) and len(tasks) >= 3:
            _unlock("parallel_master", now)
        # army_commander (25 delegate calls) is handled by thresholds

    elif tool_name == "skill_manage":
        action = str(args.get("action", ""))
        if action in ("create", "edit") or not action:
            stats["skills_created"] = stats.get("skills_created", 0) + 1
            _unlock("skill_author", now)
        created = stats.get("skills_created", 0)
        if created >= 5:
            _unlock("skill_artisan", now)
        else:
            _set_progress("skill_artisan", created, 5)
        if created >= 15:
            _unlock("skill_virtuoso", now)
        else:
            _set_progress("skill_virtuoso", created, 15)

    elif tool_name == "memory":
        # Memory Keeper is about *saving* facts — remove shouldn't count
        action = str(args.get("action", ""))
        if action in ("add", "replace") or not action:
            _unlock("memory_holder", now)

    elif tool_name in ("write_file", "patch"):
        path = str(args.get("path", "") or args.get("file_path", ""))
        content = str(args.get("content", "") or args.get("new_string", ""))
        # Plugin Developer: authoring a plugin — a plugin.yaml write must
        # look like a manifest (not e.g. editing an unrelated config)
        if "plugin.yaml" in path:
            if "name:" in content or "hooks:" in content:
                _unlock("plugin_developer", now)
        elif "/plugins/" in path:
            _unlock("plugin_developer", now)
        if "register_hook" in content:
            hooks = set(re.findall(r'register_hook\(\s*["\']([\w]+)["\']', content))
            if hooks:
                stats.setdefault("hooks_used", set()).update(hooks)
                if len(stats["hooks_used"]) >= 3:
                    _unlock("hook_master", now)

    # Tool-argument counters feed the tiered counter achievements too
    # (e.g. cron_jobs_created → Cron Master / Cron Overlord)
    _check_counter_achievements(stats, now)


def _count_user_commands(user_commands, stats, now):
    """Increment tiered counters from user command text."""
    for cmd in user_commands:
        if not isinstance(cmd, str):
            continue
        if re.search(r"hermes\s+config\s+(set|edit)\b", cmd, re.IGNORECASE):
            stats["config_changes"] = stats.get("config_changes", 0) + 1
        if re.search(r"hermes\s+plugins\s+enable\b", cmd, re.IGNORECASE):
            stats["plugins_enabled"] = stats.get("plugins_enabled", 0) + 1
        if re.search(r"hermes\s+profile\s+create\b", cmd, re.IGNORECASE):
            stats["profiles_created"] = stats.get("profiles_created", 0) + 1
        if re.search(r"hermes\s+mcp\s+add\b", cmd, re.IGNORECASE):
            stats["mcp_servers_connected"] = stats.get("mcp_servers_connected", 0) + 1
        if re.search(r"hermes\s+skills?\s+install\b", cmd, re.IGNORECASE):
            stats["skills_installed"] = stats.get("skills_installed", 0) + 1
        if "--yolo" in cmd:
            stats["yolo_tasks"] = stats.get("yolo_tasks", 0) + 1
        if re.search(r"/resume\b|--continue\b", cmd, re.IGNORECASE):
            stats["session_resumes"] = stats.get("session_resumes", 0) + 1
    _check_counter_achievements(stats, now)


# Counter-stat → [(threshold, achievement_id)] for stats-backed
# achievements. Data-driven so the thresholds are introspectable
# (render_readme.py literal-evals this to keep the README example block
# in sync) and testable, instead of 12 magic numbers scattered through
# the check function.
_COUNTER_THRESHOLDS = {
    "config_changes": [(15, "config_guru")],
    "plugins_enabled": [(5, "plugin_pack")],
    "profiles_created": [(5, "profile_collector")],
    "mcp_servers_connected": [(3, "mcp_networker")],
    "skills_installed": [(1, "skill_finder"), (5, "skill_apprentice"), (15, "skill_master")],
    "yolo_tasks": [(25, "yolo_champion")],
    "cron_jobs_created": [(5, "cron_master"), (15, "cron_overlord")],
    "session_resumes": [(10, "session_surfer")],
}


def _check_counter_achievements(stats, now):
    """Tiered achievements backed by stats counters (data-driven)."""
    for stat, tiers in _COUNTER_THRESHOLDS.items():
        value = stats.get(stat, 0)
        for threshold, aid in tiers:
            if value >= threshold:
                _unlock(aid, now)
            else:
                _set_progress(aid, value, threshold)


# ── Hook: pre_tool_call ────────────────────────────────────────────────
# Fires once per tool call BEFORE execution (block evaluation). Every tool
# call the model emitted in ONE assistant response shares the same
# api_request_id (set once per API call in conversation_loop), so counting
# consecutive calls per id reveals how many tools the model batched into a
# single step — a dimension post_tool_call cannot see (it has no
# api_request_id and cannot distinguish one response's batch from another).
#
# The tracker is transient (module-level, not persisted): the running
# count per response resets when the id changes. The persisted stat is the
# peak batch size, which is what the achievements threshold on.

_current_batch = {"id": None, "count": 0}


def _pre_tool_call(**kwargs):
    """Count tool calls batched into a single response via api_request_id."""
    req_id = kwargs.get("api_request_id") or ""
    if not req_id:
        return
    global _current_batch
    if _current_batch["id"] != req_id:
        _current_batch = {"id": req_id, "count": 1}
    else:
        _current_batch["count"] += 1
    n = _current_batch["count"]
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()
    stats["peak_tools_per_response"] = max(
        stats.get("peak_tools_per_response", 0), n
    )
    for threshold, ach_id in _BATCH_THRESHOLDS:
        if n >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, n, threshold)
            break
    _save_state()


# ── Hook: pre_llm_call ────────────────────────────────────────────────
# Fires once per turn BEFORE the tool-calling loop, with is_first_turn=True
# exactly when run_conversation was handed no prior history — i.e. a brand
# new conversation that actually reaches the model. This is the only signal
# that counts conversation starts: session creation (on_session_start) can
# fire without a message, and /new or /reset (on_session_reset) are explicit
# user rotations rather than natural context boundaries.

def _pre_llm_call(**kwargs):
    """Count fresh conversations via is_first_turn."""
    if not kwargs.get("is_first_turn"):
        return
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()
    stats["conversations_started"] = stats.get("conversations_started", 0) + 1
    cs = stats["conversations_started"]
    for threshold, ach_id in _CONVERSATION_THRESHOLDS:
        if cs >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, cs, threshold)
            break
    _save_state()


# ── Hook: transform_terminal_output ─────────────────────────────────────
# Fires per terminal command with the FULL raw output BEFORE the terminal
# tool truncates it (default ~50KiB head+tail). This is the only hook that
# sees what the model was NOT handed — output volume the command actually
# produced. Also carries env_type (local/ssh/docker/singularity/modal/
# daytona) and the numeric returncode (post_tool_call only buckets status
# ok/error — it cannot express exit code 127 "command not found").
# IMPORTANT: this is a TRANSFORM hook — returning a string would REPLACE
# the output. We are observers: always return None.

def _transform_terminal_output(**kwargs):
    """Observe raw pre-truncation output, env diversity, and exit codes."""
    output = kwargs.get("output") or ""
    env_type = kwargs.get("env_type") or "local"
    returncode = kwargs.get("returncode")

    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    # Raw output volume (bytes of the pre-truncation string)
    size = len(output.encode("utf-8", errors="ignore"))
    stats["peak_terminal_output_bytes"] = max(
        stats.get("peak_terminal_output_bytes", 0), size
    )
    if size >= _DATA_FLOOD_BYTES:
        _unlock("data_flood", now)
        _unlock("verbose_output", now)
    elif size >= _VERBOSE_OUTPUT_BYTES:
        _unlock("verbose_output", now)
    else:
        _set_progress("verbose_output", size, _VERBOSE_OUTPUT_BYTES)

    # Environment diversity (distinct execution environments)
    envs = stats.setdefault("env_types", set())
    if isinstance(envs, list):
        envs = set(envs)
        stats["env_types"] = envs
    if env_type and env_type not in envs:
        envs.add(env_type)
    for threshold, ach_id in _ENV_THRESHOLDS:
        if len(envs) >= threshold:
            _unlock(ach_id, now)
        else:
            _set_progress(ach_id, len(envs), threshold)
            break

    # Numeric exit code — 127 is the classic "command not found"
    if returncode is not None and int(returncode) == _GHOST_COMMAND_EXIT_CODE:
        _unlock("ghost_command", now)

    _save_state()
    return None  # noqa: RET501, PLR1711 — observer contract: a string here would REPLACE output


# ── Hook: transform_tool_result ─────────────────────────────────────────
# Fires per tool call with the FULL result string (post_tool_call only gets
# status/error_type — never the content). Measuring result size reveals
# context bloat: how much data a single tool pushed into the conversation.
# Also a TRANSFORM hook — observers must return None.

def _transform_tool_result(**kwargs):
    """Observe full tool-result size (context bloat)."""
    result = kwargs.get("result") or ""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    size = len(result.encode("utf-8", errors="ignore"))
    stats["peak_tool_result_bytes"] = max(
        stats.get("peak_tool_result_bytes", 0), size
    )
    if size >= _COLOSSAL_RESULT_BYTES:
        _unlock("colossal_result", now)
        _unlock("big_haul", now)
    elif size >= _BIG_HAUL_BYTES:
        _unlock("big_haul", now)
    else:
        _set_progress("big_haul", size, _BIG_HAUL_BYTES)

    _save_state()
    return None  # noqa: RET501, PLR1711 — observer contract: a string here would REPLACE result


# ── Hook: post_llm_call ─────────────────────────────────────────────────
# Fires once per turn after the tool-calling loop completes. Has
# conversation_history with tool_calls; tool counting itself lives in
# post_tool_call, this hook handles per-turn signals.

def _post_llm_call(**kwargs):
    """Detect per-turn achievements (messages, models, platforms, commands)."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    conversation_history = kwargs.get("conversation_history", [])
    user_message = kwargs.get("user_message", "")
    model = kwargs.get("model", "")
    platform = kwargs.get("platform", "")

    # Track model + platform (cumulative across sessions, persisted)
    if model and model not in ("unknown", ""):
        stats.setdefault("models_used", set()).add(model)
    if platform and platform not in ("unknown", "none", ""):
        stats.setdefault("platforms", set()).add(platform)
    else:
        stats.setdefault("platforms", set()).add("cli")

    # Track multi-lingual: non-ASCII alphabetic chars in user message
    if user_message and any(ord(c) > 0x7F for c in user_message if c.isalpha()):
        _unlock("multi_lingual", now)

    # ── Message verbosity: word count of the user's message ────
    # A long single message means the user wrote a detailed spec instead
    # of drip-feeding context — a distinct dimension from message counts.
    if isinstance(user_message, str) and user_message.strip():
        word_count = len(user_message.split())
        stats["longest_message_words"] = max(
            stats.get("longest_message_words", 0), word_count
        )
        if word_count >= _NOVELIST_WORDS:
            _unlock("novelist", now)
            _unlock("wordsmith", now)
        elif word_count >= _WORDSMITH_WORDS:
            _unlock("wordsmith", now)
        else:
            _set_progress("wordsmith", word_count, _WORDSMITH_WORDS)

    # ── Model-response verbosity: word count of the assistant reply ──
    # Mirrors the user-verbosity dimension but for the model's OWN output —
    # how much it wrote in a single response (assistant_response), distinct
    # from user input length. A novel-length response means a deep analysis
    # or long-form generation was requested.
    assistant_response = kwargs.get("assistant_response", "")
    if isinstance(assistant_response, str) and assistant_response.strip():
        resp_words = len(assistant_response.split())
        stats["longest_response_words"] = max(
            stats.get("longest_response_words", 0), resp_words
        )
        if resp_words >= _NOVEL_AUTHOR_WORDS:
            _unlock("novel_author", now)
            _unlock("essayist", now)
        elif resp_words >= _ESSAYIST_WORDS:
            _unlock("essayist", now)
        else:
            _set_progress("essayist", resp_words, _ESSAYIST_WORDS)

    # Extract the current turn's user content for command detection
    user_commands = []
    slash_cmds_this_turn = set()
    for msg in reversed(conversation_history):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            if isinstance(content, str):
                user_commands.append(content)
                for token in content.split():
                    # Canonical form: command name WITHOUT leading "/"
                    # (matches get_command() on pre_gateway_dispatch, so the
                    # same command typed both ways dedupes in one set).
                    if token.startswith("/") and len(token) > 1:
                        slash_cmds_this_turn.add(token[1:].lower())
            break  # turn boundary — only the current user message

    # Track slash commands (cumulative)
    for sc in slash_cmds_this_turn:
        stats.setdefault("slash_commands_used", set()).add(sc)

    # ── Message thresholds (cumulative; post_llm_call fires 1/turn) ──
    stats["total_turns"] = stats.get("total_turns", 0) + 1
    _check_message_thresholds(stats.get("total_turns", 0), now)

    # ── User-command achievements ──────────────────────────────
    for cmd in user_commands:
        for ach_id, patterns in TERMINAL_PATTERNS.items():
            for pat in patterns:
                if pat.search(cmd):
                    _unlock(ach_id, now)
    _count_user_commands(user_commands, stats, now)

    # ── Model Hopper: 2+ models (cumulative, persisted) ────────
    # Use stats["models_used"] (persisted to state.json) rather than the
    # in-memory sets so progress survives gateway restarts.
    num_models = len(stats.get("models_used", set()))
    if num_models >= 2:
        _unlock("model_hopper", now)
    if num_models >= 5:
        _unlock("multi_model", now)
    else:
        _set_progress("multi_model", num_models, 5)
    if num_models >= 10:
        _unlock("model_collector", now)
    else:
        _set_progress("model_collector", num_models, 10)

    # ── Cross-Platform: 2+ platforms (cumulative, persisted) ──
    num_platforms = len(stats.get("platforms", set()))
    if num_platforms >= 2:
        _unlock("cross_platform", now)
    if num_platforms >= 3:
        _unlock("gateway_networker", now)
    else:
        _set_progress("gateway_networker", num_platforms, 3)
    if num_platforms >= 5:
        _unlock("cross_platform_veteran", now)
    else:
        _set_progress("cross_platform_veteran", num_platforms, 5)

    # ── Gateway Guru: non-CLI platform ─────────────────────────
    non_cli = {p for p in stats.get("platforms", set())
               if p not in ("cli", "unknown", "none", "")}
    if non_cli:
        _unlock("gateway_guru", now)

    # ── Early Bird / Night Owl ──────────────────────────────────
    local_hour = datetime.now().hour  # noqa: DTZ005 — local-time achievements
    if local_hour < 6:
        _unlock("early_bird", now)
    if 0 <= local_hour < 5:
        _unlock("night_owl", now)

    # ── Slash Commander: 3+ different slash commands ────────────
    num_slash = len(stats.get("slash_commands_used", set()))
    if num_slash >= 3:
        _unlock("slash_commander", now)
    else:
        _set_progress("slash_commander", num_slash, 3)

    # ── First Steps: first user message ────────────────────────
    if user_message and user_message.strip():
        _unlock("first_steps", now)

    # ── Persistent: 3+ sessions ────────────────────────────────
    total_sessions = stats.get("total_sessions", 0)
    if total_sessions >= 3:
        _unlock("persistent", now)
    else:
        _set_progress("persistent", total_sessions, 3)

    # ── Group & rarity completions ─────────────────────────────
    _check_group_completions()
    _check_completionist()

    # Persist state (forced — turn boundary)
    _save_state(force=True)


# ── Hook: post_api_request ───────────────────────────────────────────────
# Fires once per successful provider API request inside the agent loop.
# Carries normalized usage (input/output/cache token buckets with computed
# total_tokens) and api_duration in seconds — powers the token-consumption
# milestones and the fast-response achievement.

def _post_api_request(**kwargs):
    """Detect token milestones, fast API responses, and provider diversity."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    # ── Provider diversity (cumulative, persisted) ─────────────
    provider = kwargs.get("provider", "")
    if provider and provider not in ("unknown", ""):
        stats.setdefault("providers_used", set()).add(provider)
        num_providers = len(stats["providers_used"])
        if num_providers >= 2:
            _unlock("provider_hopper", now)
        if num_providers >= 5:
            _unlock("provider_collector", now)
        else:
            _set_progress("provider_collector", num_providers, 5)

    # ── Deep Dive: 10 API steps in a single turn ───────────────
    # api_call_count resets to 0 at the start of every user turn and
    # increments per provider call — a count ≥ 10 means the agent ran a
    # long autonomous multi-step stretch without user intervention.
    call_count = kwargs.get("api_call_count")
    if isinstance(call_count, (int, float)) and call_count >= _DEEP_DIVE_STEPS:
        _unlock("deep_dive", now)

    # ── Context depth: messages in this single API request ─────
    # message_count = len(api_messages) — the full conversation history
    # (system prompt + turns + tool results) sent to the model in ONE
    # request. A high value means the model had to chew through a long
    # context at once — distinct from cumulative turn counts.
    message_count = kwargs.get("message_count")
    if isinstance(message_count, (int, float)) and message_count > 0:
        stats["peak_context_messages"] = max(
            stats.get("peak_context_messages", 0), int(message_count)
        )
        if message_count >= _CONTEXT_COLOSSUS_MESSAGES:
            _unlock("context_colossus", now)
            _unlock("deep_context", now)
        elif message_count >= _DEEP_CONTEXT_MESSAGES:
            _unlock("deep_context", now)
        else:
            _set_progress("deep_context", int(message_count), _DEEP_CONTEXT_MESSAGES)

    usage = kwargs.get("usage")
    total_tokens = 0
    if isinstance(usage, dict):
        total_tokens = usage.get("total_tokens") or 0
        if not total_tokens:
            total_tokens = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    if total_tokens:
        stats["total_tokens"] = stats.get("total_tokens", 0) + int(total_tokens)
        _check_token_thresholds(stats["total_tokens"], now)

    duration = kwargs.get("api_duration")
    if (
        isinstance(duration, (int, float))
        and duration >= 0
        and duration < _FAST_RESPONSE_THRESHOLD_S
    ):
        stats["fast_requests"] = stats.get("fast_requests", 0) + 1
        if stats["fast_requests"] >= _FAST_RESPONSE_COUNT:
            _unlock("speed_demon", now)
        else:
            _set_progress("speed_demon", stats["fast_requests"], _FAST_RESPONSE_COUNT)

    # ── Output-cap truncation: finish_reason="length" ──────────
    # The model hit its max output tokens and was cut off mid-response.
    # A real, recognizable event: usage buckets show the tokens consumed,
    # but only finish_reason reveals the response was INCOMPLETE.
    finish_reason = kwargs.get("finish_reason", "")
    if finish_reason and str(finish_reason).lower() == "length":
        stats["truncated_responses"] = stats.get("truncated_responses", 0) + 1
        trunc = stats["truncated_responses"]
        for threshold, ach_id in _TRUNCATION_THRESHOLDS:
            if trunc >= threshold:
                _unlock(ach_id, now)
            else:
                _set_progress(ach_id, trunc, threshold)
                break

    _save_state()


# ── Hook: pre_api_request ────────────────────────────────────────────────
# Fires BEFORE each provider API request inside the agent loop. Carries
# base_url (the endpoint host — local/self-hosted vs cloud), and
# approx_input_tokens (the preflight estimate of input tokens for THIS
# request). The post hook sees cumulative totals; this one sees the
# single-request spike and the endpoint topology.

def _is_local_base_url(base_url):
    """True when base_url points at a loopback/private/self-hosted host."""
    if not isinstance(base_url, str) or not base_url.strip():
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        host = ""
    if not host:
        return False
    return (
        host in _LOCAL_HOST_MARKERS
        or any(host.startswith(p) for p in _LOCAL_PREFIXES)
        or any(host.endswith(s) for s in _LOCAL_SUFFIXES)
    )


def _pre_api_request(**kwargs):
    """Detect local-model usage and single-request input-token spikes."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    # ── Local/self-hosted endpoints (Local First / Self-Hosted) ─
    base_url = kwargs.get("base_url", "")
    if _is_local_base_url(base_url):
        stats["local_requests"] = stats.get("local_requests", 0) + 1
        local_count = stats["local_requests"]
        _unlock("local_first", now)
        if local_count >= _SELF_HOSTED_REQUESTS:
            _unlock("self_hosted", now)
        else:
            _set_progress("self_hosted", local_count, _SELF_HOSTED_REQUESTS)

    # ── Single-request input-token spike (Context Monster/Tsunami)
    # approx_input_tokens is the preflight estimate for THIS request —
    # a huge value means the model loaded a massive context window at
    # once, distinct from cumulative token milestones.
    approx = kwargs.get("approx_input_tokens")
    if isinstance(approx, (int, float)) and approx > 0:
        stats["peak_input_tokens"] = max(
            stats.get("peak_input_tokens", 0), int(approx)
        )
        if approx >= _TOKEN_TSUNAMI_INPUT_TOKENS:
            _unlock("token_tsunami", now)
            _unlock("context_monster", now)
        elif approx >= _CONTEXT_MONSTER_INPUT_TOKENS:
            _unlock("context_monster", now)
        else:
            _set_progress(
                "context_monster", int(approx), _CONTEXT_MONSTER_INPUT_TOKENS
            )

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Hook: on_session_start ───────────────────────────────────────────────
# Fired once when a brand-new session is created (not on continuation).

def _on_session_start(**kwargs):
    """Count distinct sessions (powers the Persistent / session milestones).

    Idempotent per session: the gateway delivers ``session_id`` with the
    event, so a re-delivery of the SAME session start (crash-recovery
    retry, hook double-fire) cannot inflate ``total_sessions``. Sessions
    without an id (older gateways, synthetic calls) fall back to counting
    every firing. ``model``/``platform`` are deliberately NOT read here —
    a session that never reaches the LLM has no model usage to record,
    and ``on_session_end``/``post_llm_call`` already persist them.
    """
    state = _load_state()
    stats = state.setdefault("stats", {})
    session_id = kwargs.get("session_id")
    if not session_id:
        stats["total_sessions"] = stats.get("total_sessions", 0) + 1
        _save_state(force=True)
        return
    if stats.get("last_session_id") == session_id:
        return  # re-delivery of an already-counted session start
    stats["last_session_id"] = session_id
    stats["total_sessions"] = stats.get("total_sessions", 0) + 1
    _save_state(force=True)


# ── Hook: on_session_end ─────────────────────────────────────────────────
# Fired at the very end of every run_conversation(). Metadata only.

def _on_session_end(**kwargs):
    """Track session metadata, streaks, and session-based achievements."""
    state = _load_state()
    stats = state.setdefault("stats", {})

    model = kwargs.get("model", "")
    platform = kwargs.get("platform", "")

    # Track model + platform
    if model and model not in ("unknown", ""):
        stats.setdefault("models_used", set()).add(model)
    if platform and platform not in ("unknown", "none", ""):
        stats.setdefault("platforms", set()).add(platform)
    else:
        stats.setdefault("platforms", set()).add("cli")

    # ── Streak tracking (daily consecutive usage) ───────────────
    today = date.today().isoformat()  # noqa: DTZ011 — streaks are local-calendar
    last_active = stats.get("last_active_date")
    current_streak = stats.get("current_streak", 0)

    if last_active == today:
        # Already counted today
        pass
    elif last_active is not None:
        from datetime import timedelta
        try:
            last_date = date.fromisoformat(last_active) if isinstance(last_active, str) else last_active
            yesterday = date.today() - timedelta(days=1)  # noqa: DTZ011
            if last_date == yesterday:
                current_streak += 1
            elif last_date < yesterday:
                current_streak = 1  # reset
            else:
                current_streak = 1  # shouldn't happen, but safety
        except (ValueError, TypeError):
            current_streak = 1
    else:
        current_streak = 1

    stats["current_streak"] = current_streak
    stats["last_active_date"] = today
    stats["longest_streak"] = max(stats.get("longest_streak", 0), current_streak)

    # ── Check streak achievements ───────────────────────────────
    _check_streaks(stats, datetime.now(UTC).isoformat())

    # ── Group completions (re-check after session milestones) ───
    _check_group_completions()
    _check_completionist()

    _save_state(force=True)


# ── Hook: subagent_stop ─────────────────────────────────────────────────
# Fires once per child agent after delegate_task finishes, with
# child_role ("leaf"/"orchestrator"), child_status ("completed"/"failed"/
# "interrupted"/"error"), and duration_ms. This is the authoritative
# count of subagents *spawned* (a single delegate_task with 3 tasks
# spawns 3 children) — replacing the old delegate_task-call heuristic.

def _on_subagent_stop(**kwargs):
    """Count actual subagent children, orchestrator usage, and failures."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    child_role = kwargs.get("child_role", "")
    child_status = kwargs.get("child_status", "")

    # Every child that stopped was spawned — this is the true subagent count
    stats["subagents_spawned"] = stats.get("subagents_spawned", 0) + 1
    spawned = stats["subagents_spawned"]
    if spawned >= 25:
        _unlock("army_commander", now)
    else:
        _set_progress("army_commander", spawned, 25)

    # Concurrency: one child just finished — decrement the live counter.
    # subagent_start increments it; the peak is what Conductor measures.
    live = stats.get("concurrent_subagents", 0)
    stats["concurrent_subagents"] = max(0, live - 1)

    # Orchestrator: child used the orchestrator role
    if child_role and "orchestrator" in str(child_role).lower():
        _unlock("orchestrator", now)

    # Resilient: a subagent failed/interrupted — user kept going
    if child_status and str(child_status).lower() in ("failed", "error", "interrupted"):
        stats["subagents_failed"] = stats.get("subagents_failed", 0) + 1
        _unlock("resilient", now)

    # Subagent runtime: how long the child actually ran (duration_ms).
    # Subagent counting cannot see this — a 10-minute child is a very
    # different delegation than a 10-second one.
    duration_ms = kwargs.get("duration_ms")
    if isinstance(duration_ms, (int, float)) and duration_ms >= 0:
        stats["longest_subagent_ms"] = max(
            stats.get("longest_subagent_ms", 0), int(duration_ms)
        )
        longest = stats["longest_subagent_ms"]
        if longest >= _MARATHON_SUBAGENT_MS:
            _unlock("marathon", now)
            _unlock("slow_thinker", now)
        elif longest >= _SLOW_SUBAGENT_MS:
            _unlock("slow_thinker", now)
        else:
            _set_progress("slow_thinker", longest, _SLOW_SUBAGENT_MS)

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Hook: subagent_start ────────────────────────────────────────────────
# Fires when a subagent is spawned (before it runs). Pairs with
# subagent_stop to track TRUE concurrency: children alive at the same
# time (multiple delegate_task calls overlapping, or batched spawns).

def _on_subagent_start(**kwargs):
    """Track peak concurrent subagent count (Conductor)."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    live = stats.get("concurrent_subagents", 0) + 1
    stats["concurrent_subagents"] = live
    peak = max(stats.get("max_concurrent_subagents", 0), live)
    stats["max_concurrent_subagents"] = peak

    if peak >= 3:
        _unlock("conductor", now)
    else:
        _set_progress("conductor", peak, 3)

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Hook: api_request_error ────────────────────────────────────────────
# Fires when the LLM provider call fails (invalid response, 429/402,
# timeout, retries exhausted). The agent keeps going — Indestructible
# rewards surviving these without quitting.

def _on_api_request_error(**kwargs):
    """Count LLM API errors the agent survived + sustained-failure depth."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    stats["api_errors"] = stats.get("api_errors", 0) + 1
    errors = stats["api_errors"]

    if errors >= 10:
        _unlock("indestructible", now)
    else:
        _set_progress("indestructible", errors, 10)

    # Sustained-failure depth: retry_count = consecutive failures of the
    # SAME request before this hook fired. Track the deepest one seen.
    retry_depth = 0
    try:
        retry_depth = int(kwargs.get("retry_count") or 0)
    except (TypeError, ValueError):
        retry_depth = 0
    if retry_depth > 0:
        stats["max_retry_depth"] = max(stats.get("max_retry_depth", 0), retry_depth)
        if retry_depth >= _UNDETERRED_RETRY_DEPTH:
            _unlock("undeterred", now)
        if retry_depth >= _TENACIOUS_RETRY_DEPTH:
            _unlock("tenacious", now)
        else:
            _set_progress("tenacious", retry_depth, _TENACIOUS_RETRY_DEPTH)

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Hook: pre_approval_request ──────────────────────────────────────────
# Fires when an approval prompt is raised (before the user answers).
# Counts how often the user's commands trigger approval gates, with
# command + surface (cli/gateway). Under Scrutiny rewards hitting 10.

def _on_approval_request(**kwargs):
    """Count approval requests the user triggered."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    stats["approval_requests"] = stats.get("approval_requests", 0) + 1
    requests = stats["approval_requests"]

    if requests >= 10:
        _unlock("under_scrutiny", now)
    else:
        _set_progress("under_scrutiny", requests, 10)

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Hook: post_approval_response ────────────────────────────────────────
# Fires after the user responds to an approval prompt, with
# choice: "once" | "session" | "always" | "deny" | "timeout",
# surface: "cli" | "gateway", pattern_key + pattern_keys (danger classes).

def _on_approval_response(**kwargs):
    """Detect approval behavior: permanent trust, denial, yolo mode,
    remote approvals (gateway surface), and danger-class diversity."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    choice = str(kwargs.get("choice", ""))

    if choice == "always":
        stats["approvals_always"] = stats.get("approvals_always", 0) + 1
        _unlock("trust_fall", now)
        # Approving permanently is the real-world equivalent of --yolo:
        # the command will never prompt again.
        stats["yolo_tasks"] = stats.get("yolo_tasks", 0) + 1
        _unlock("yolo_mode", now)
        if stats["yolo_tasks"] >= 25:
            _unlock("yolo_champion", now)
        else:
            _set_progress("yolo_champion", stats["yolo_tasks"], 25)
    elif choice == "deny":
        stats["approvals_denied"] = stats.get("approvals_denied", 0) + 1
        _unlock("cautious", now)

    # ── Approval-context dimension ────────────────────────────
    # Only a positive answer is an approval; deny/timeout are not.
    if choice in ("once", "session", "always"):
        # Surface: where the user answered. Gateway = remote approval.
        if kwargs.get("surface") == "gateway":
            stats["approvals_gateway"] = stats.get("approvals_gateway", 0) + 1
            gateway_count = stats["approvals_gateway"]
            for threshold, ach_id in _APPROVAL_SURFACE_THRESHOLDS:
                if gateway_count >= threshold:
                    _unlock(ach_id, now)
                else:
                    _set_progress(ach_id, gateway_count, threshold)

        # Danger-class diversity: distinct patterns approved. pattern_keys
        # is a list (one command can match several classes); dedupe into
        # a persisted set so repeat approvals of the same class add nothing.
        pattern_keys = kwargs.get("pattern_keys") or []
        if pattern_keys:
            approved = stats.setdefault("approved_patterns", set())
            if not isinstance(approved, set):
                approved = set(approved)
                stats["approved_patterns"] = approved
            approved.update(k for k in pattern_keys if k)
            distinct = len(approved)
            for threshold, ach_id in _APPROVAL_PATTERN_THRESHOLDS:
                if distinct >= threshold:
                    _unlock(ach_id, now)
                else:
                    _set_progress(ach_id, distinct, threshold)

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Hook: on_session_reset ──────────────────────────────────────────────
# Fires when the gateway swaps in a new session key — user invoked
# /new, /reset, /clear, or the adapter rotated after an idle window.

def _on_session_reset(**kwargs):
    """Count fresh-session rotations."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    stats["session_resets"] = stats.get("session_resets", 0) + 1
    _unlock("fresh_start", datetime.now(UTC).isoformat())
    _save_state()


# ── Hook: on_session_finalize ───────────────────────────────────────────
# Fires when the gateway shuts down an agent or a session's reset policy
# expires. The debounced state save may have a pending write and the
# notification debounce window may still hold undelivered unlocks — both
# must be flushed NOW, before the process exits.

def _on_session_finalize(**kwargs):
    """Flush pending state writes and queued notifications."""
    try:
        _save_state(force=True)
    except Exception:  # noqa: BLE001, S110 — finalize must never crash shutdown
        pass
    try:
        _flush_notification_queue()
    except Exception:  # noqa: BLE001, S110 — finalize must never crash shutdown
        pass


# ── Hook: pre_gateway_dispatch ─────────────────────────────────────────
# Fires once per incoming user-originated message (after the internal-event
# guard, before auth/dispatch). The full MessageEvent is available; its
# `source` carries platform + user identity. This is the ONLY hook that
# sees messages from OTHER users — everything else fires for agent turns.
# Drives Social Butterfly / Party Host (distinct senders seen).

def _on_pre_gateway_dispatch(**kwargs):
    """Track distinct users who message the gateway (Social Butterfly)."""
    event = kwargs.get("event")
    if not event:
        return
    # Only user-originated messages carry a real source; internal events
    # are filtered upstream but be defensive anyway.
    if getattr(event, "internal", False):
        return
    source = getattr(event, "source", None)
    if not source:
        return
    if getattr(source, "is_bot", False):
        return

    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    # Identity key: platform + stable user id (fall back to name)
    platform = str(getattr(source, "platform", "") or "")
    user_id = getattr(source, "user_id", None) or getattr(source, "user_name", None)
    if not user_id:
        return
    users_seen = stats.setdefault("users_seen", set())
    users_seen.add(f"{platform}:{user_id}")
    n = len(users_seen)

    if n >= 3:
        _unlock("social_butterfly", now)
    else:
        _set_progress("social_butterfly", n, 3)
    if n >= 10:
        _unlock("party_host", now)
    else:
        _set_progress("party_host", n, 10)

    # ── Media messages (Show and Tell / Visual Storyteller) ─────
    # The MessageEvent carries media_urls (local file paths for the vision
    # tool) and media_types; message_type can be PHOTO/VIDEO/AUDIO/DOCUMENT
    # etc. instead of TEXT. Any of these signals counts as a media message —
    # a genuinely distinct usage dimension (sending files/images, not text).
    has_media = False
    media_urls = getattr(event, "media_urls", None) or []
    media_types = getattr(event, "media_types", None) or []
    if media_urls or media_types:
        has_media = True
    else:
        msg_type = getattr(event, "message_type", None)
        if msg_type is not None:
            type_val = str(getattr(msg_type, "value", msg_type)).lower()
            if type_val not in ("", "text", "command"):
                has_media = True
    if has_media:
        stats["media_messages"] = stats.get("media_messages", 0) + 1
        media_count = stats["media_messages"]
        for threshold, ach_id in _MEDIA_THRESHOLDS:
            if media_count >= threshold:
                _unlock(ach_id, now)
            else:
                _set_progress(ach_id, media_count, threshold)

    # ── Gateway-intercepted slash commands (Command Center/General) ──
    # Slash commands the USER types are intercepted by the gateway BEFORE
    # the LLM (/new, /reset, /title, /model, /achievements...) — they never
    # reach post_llm_call, so the LLM-path slash_commander could never
    # count the plugin's own command. The MessageEvent carries
    # is_command()/get_command() so they ARE observable here.
    # Attribution: this hook fires for ALL users (shared channels) BEFORE
    # auth, so only commands from the platform's PRIMARY user count — the
    # first non-bot user seen, which is the owner in every real deployment.
    # Other users' commands must not unlock the user's achievements.
    primary_users = stats.setdefault("primary_users", {})
    user_key = f"{platform}:{user_id}"
    primary_users.setdefault(platform, user_key)
    if primary_users.get(platform) == user_key:
        cmd = None
        get_cmd = getattr(event, "get_command", None)
        if callable(get_cmd):
            try:
                cmd = get_cmd()
            except Exception:  # noqa: BLE001 — a broken adapter method must not crash the hook
                cmd = None
        if not cmd:
            # Fallback: parse the raw text for a leading "/" token
            text = getattr(event, "text", "") or ""
            if text.startswith("/"):
                cmd = text.split(maxsplit=1)[0][1:].lower()
        if cmd:
            slash_used = stats.setdefault("slash_commands_used", set())
            slash_used.add(cmd)
            num_slash = len(slash_used)
            if num_slash >= 3:
                _unlock("slash_commander", now)
            else:
                _set_progress("slash_commander", num_slash, 3)
            for threshold, ach_id in _GATEWAY_COMMAND_THRESHOLDS:
                if num_slash >= threshold:
                    _unlock(ach_id, now)
                else:
                    _set_progress(ach_id, num_slash, threshold)

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Slash Command Handlers ──────────────────────────────────────────────

def _progress_bar(current, target, width=10):
    if target <= 0:
        return "░" * width
    filled = min(int(current / target * width), width)
    return "█" * filled + "░" * (width - filled)

def _format_badge(a_id, a_def, state, compact=False) -> str:
    """Render one achievement badge line.

    compact=True drops the description and full progress bar — used by the
    group-filter view so large groups (Power User is 44) stay under
    Discord's 2000-char cap in every locale. Details remain available via
    ``/achievement <id>``.
    """
    a_state = state.get("achievements", {}).get(a_id, {})
    unlocked = bool(a_state.get("unlocked"))
    progress = a_state.get("progress")
    secret = a_def.get("secret", False) or a_def.get("hidden", False)
    locale = state.get("locale", "en")

    if unlocked:
        icon = "✅"
    elif secret:
        icon = "❓"
    else:
        icon = "⬜"

    # Locked secrets hide both name and description (Steam-style "???")
    if secret and not unlocked:
        name_str = "???"
        desc_str = "???"
    else:
        name_str = _t(f"achievement.{a_id}.name", locale)
        desc_str = _t(f"achievement.{a_id}.description", locale)

    prog_str = ""
    if progress and not unlocked:
        cur = progress.get("current", 0)
        tgt = progress.get("target", 1)
        if compact:
            prog_str = f" — {cur}/{tgt}"
        else:
            bar = _progress_bar(cur, tgt)
            prog_str = _t("ui.detail_progress", locale, bar=bar, current=cur, target=tgt, percent=int(cur/tgt*100))

    if compact:
        return _t("ui.badge_compact_format", locale, icon=icon, name=name_str, progress=prog_str)
    return _t("ui.badge_format", locale, icon=icon, name=name_str, description=desc_str, progress=prog_str)


def _next_up_candidates(state):
    """(pct, aid, cur, tgt) for every locked, non-secret achievement that
    has progress, sorted by pct descending (stable — ties keep
    ACHIEVEMENT_DEFS order, so the one-line hint and the full next-up
    view can never disagree about the top candidate). Shared by both."""
    ach_state = state.get("achievements", {})
    candidates = []
    for aid, a_def in ACHIEVEMENT_DEFS.items():
        s = ach_state.get(aid, {})
        if s.get("unlocked"):
            continue
        # Locked secrets never leak progress in the "next up" view
        if a_def.get("secret", False) or a_def.get("hidden", False):
            continue
        prog = s.get("progress")
        if not prog or not prog.get("target"):
            continue
        cur = prog.get("current", 0)
        tgt = prog.get("target", 1)
        pct = cur / tgt
        if pct >= 1.0:  # meets threshold but unlock check hasn't fired yet
            continue
        candidates.append((pct, aid, cur, tgt))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates


def _handle_next_up(state) -> str:
    """Show the achievements closest to unlocking (by progress %)."""
    locale = state.get("locale", "en")
    candidates = _next_up_candidates(state)
    if not candidates:
        return _t("ui.next_empty", locale)
    lines = [_t("ui.next_title", locale) + "\n"]
    for pct, aid, cur, tgt in candidates[:3]:
        a_def = ACHIEVEMENT_DEFS[aid]
        rarity_e = RARITY_EMOJIS.get(a_def.get("rarity", "common"), "⬜")
        name = _t(f"achievement.{aid}.name", locale)
        bar = _progress_bar(cur, tgt)
        pct_int = int(pct * 100)
        lines.append(
            f"{rarity_e} **{name}** — {bar} {cur}/{tgt} ({pct_int}%)"
        )
    return "\n".join(lines)


def _next_up_hint(state) -> str:
    """One-line teaser of the closest-to-unlock achievement (default view)."""
    locale = state.get("locale", "en")
    candidates = _next_up_candidates(state)
    if not candidates:
        return ""
    pct, aid, cur, tgt = candidates[0]
    name = _t(f"achievement.{aid}.name", locale)
    bar = _progress_bar(cur, tgt)
    pct_int = int(pct * 100)
    return _t("ui.next_hint", locale, name=name, bar=bar,
              current=cur, target=tgt, percent=pct_int)


def _format_bytes(n: int) -> str:
    """Human-readable byte size (e.g. 1.5 MiB)."""
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    units = ("KiB", "MiB", "GiB", "TiB")
    size = float(n)
    for unit in units:
        size /= 1024.0
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.1f} {unit}"
    raise AssertionError("unreachable: units[-1] always returns")  # pragma: no cover


def _format_duration(ms: int) -> str:
    """Human-readable duration from milliseconds (e.g. 12m 30s)."""
    ms = int(ms or 0)
    total_s = max(0, ms // 1000)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _handle_achievements(raw_args: str) -> str:
    args = raw_args.strip().lower()
    state = _load_state()
    ach_state = state.get("achievements", {})
    unlocked_ids = {a_id for a_id, s in ach_state.items() if s.get("unlocked")}
    newly = state.get("newly_unlocked", [])
    stats = state.get("stats", {})
    locale = state.get("locale", "en")

    if args == "lang" or args.startswith("lang "):
        return _handle_lang(args[5:] if args.startswith("lang ") else "")

    if args == "next":
        return _handle_next_up(state)

    if args == "recent":
        if not newly and not unlocked_ids:
            return _t("ui.no_achievements", locale)
        lines = [_t("ui.recent_title", locale) + "\n"]
        if newly:
            # Cap at 10 — the default view shows 3, but the explicit
            # 'recent' command may show more. Still bounded so the output
            # never exceeds Discord's 2000-char message cap.
            recent_ids = newly[-10:]
        else:
            # Fall back to the most recently unlocked by unlocked_at
            recent_ids = sorted(
                (aid for aid, s in ach_state.items()
                 if s.get("unlocked") and s.get("unlocked_at")),
                key=lambda aid: ach_state[aid].get("unlocked_at", ""),
                reverse=True,
            )[:3]
        for a_id in recent_ids:
            a_def = ACHIEVEMENT_DEFS.get(a_id)
            if a_def:
                rarity_e = RARITY_EMOJIS.get(a_def.get("rarity", "common"), "⬜")
                ach_name = _t(f"achievement.{a_id}.name", locale)
                ach_desc = _t(f"achievement.{a_id}.description", locale)
                lines.append(f"{rarity_e} **{ach_name}** — {ach_desc}")
        return "\n".join(lines)

    if args == "stats":
        total = len(ACHIEVEMENT_DEFS)
        uc = len(unlocked_ids)
        pct = round(uc / total * 100) if total else 0
        lines = [_t("ui.stats_title", locale) + "\n" + _t("ui.stats_header", locale)]
        lines.append(_t("ui.stats_unlocked", locale, unlocked=uc, total=total, percent=pct))
        if stats:
            lines.append(_t("ui.stats_total_turns", locale, count=stats.get("total_turns", 0)))
            if stats.get("total_tokens"):
                lines.append(_t("ui.stats_tokens", locale, count=stats.get("total_tokens", 0)))
            tools = stats.get("tools_used", {})
            lines.append(_t("ui.stats_unique_tools", locale, count=len(tools)))
            lines.append(_t("ui.stats_total_calls", locale, count=sum(tools.values())))
            lines.append(_t("ui.stats_sessions", locale, count=stats.get("total_sessions", 0)))
            lines.append(_t("ui.stats_streak", locale, count=stats.get("current_streak", 0)))
            # Live session summary
            active = stats.get("active_session") or {}
            if active.get("calls"):
                lines.append(_t("ui.stats_session_tools", locale,
                                calls=active.get("calls", 0),
                                tools=len(active.get("tool_names") or [])))
            # Tier progress counters
            if stats.get("cron_jobs_created"):
                lines.append(_t("ui.stats_cron", locale, count=stats.get("cron_jobs_created", 0)))
            if stats.get("skills_created"):
                lines.append(_t("ui.stats_skills_created", locale, count=stats.get("skills_created", 0)))
            if stats.get("config_changes"):
                lines.append(_t("ui.stats_config", locale, count=stats.get("config_changes", 0)))
            # Authoritative subagent count (children, not delegate_task calls)
            if stats.get("subagents_spawned"):
                lines.append(_t("ui.stats_delegated", locale, count=stats.get("subagents_spawned", 0)))
            if stats.get("approvals_always"):
                lines.append(_t("ui.stats_approvals_always", locale, count=stats.get("approvals_always", 0)))
            if stats.get("approvals_denied"):
                lines.append(_t("ui.stats_approvals_denied", locale, count=stats.get("approvals_denied", 0)))
            if stats.get("approval_requests"):
                lines.append(_t("ui.stats_approval_requests", locale, count=stats.get("approval_requests", 0)))
            if stats.get("approvals_gateway"):
                lines.append(_t("ui.stats_approvals_gateway", locale, count=stats.get("approvals_gateway", 0)))
            if stats.get("approved_patterns"):
                lines.append(_t("ui.stats_approved_patterns", locale, count=len(stats.get("approved_patterns", set()))))
            if stats.get("max_concurrent_subagents"):
                lines.append(_t("ui.stats_max_concurrent", locale, count=stats.get("max_concurrent_subagents", 0)))
            if stats.get("api_errors"):
                lines.append(_t("ui.stats_api_errors", locale, count=stats.get("api_errors", 0)))
            if stats.get("users_seen"):
                lines.append(_t("ui.stats_users_seen", locale, count=len(stats.get("users_seen", set()))))
            if stats.get("session_resets"):
                lines.append(_t("ui.stats_session_resets", locale, count=stats.get("session_resets", 0)))
            if stats.get("conversations_started"):
                lines.append(_t("ui.stats_conversations", locale, count=stats.get("conversations_started", 0)))
            if stats.get("peak_tools_per_response"):
                lines.append(_t("ui.stats_peak_batch", locale, count=stats.get("peak_tools_per_response", 0)))
            if stats.get("media_messages"):
                lines.append(_t("ui.stats_media", locale, count=stats.get("media_messages", 0)))
            if stats.get("peak_context_messages"):
                lines.append(_t("ui.stats_peak_context", locale, count=stats.get("peak_context_messages", 0)))
            if stats.get("peak_input_tokens"):
                lines.append(_t("ui.stats_peak_input", locale, count=stats.get("peak_input_tokens", 0)))
            if stats.get("local_requests"):
                lines.append(_t("ui.stats_local_requests", locale, count=stats.get("local_requests", 0)))
            if stats.get("peak_terminal_output_bytes"):
                lines.append(_t("ui.stats_peak_terminal_output", locale,
                                size=_format_bytes(stats.get("peak_terminal_output_bytes", 0))))
            if stats.get("peak_tool_result_bytes"):
                lines.append(_t("ui.stats_peak_tool_result", locale,
                                size=_format_bytes(stats.get("peak_tool_result_bytes", 0))))
            env_types = stats.get("env_types", set())
            if isinstance(env_types, set):
                env_types = sorted(env_types)
            if env_types:
                lines.append(_t("ui.stats_env_types", locale,
                                count=len(env_types), envs=", ".join(env_types)))
            if stats.get("longest_response_words"):
                lines.append(_t("ui.stats_longest_response", locale,
                                count=stats.get("longest_response_words", 0)))
            if stats.get("truncated_responses"):
                lines.append(_t("ui.stats_truncations", locale,
                                count=stats.get("truncated_responses", 0)))
            if stats.get("longest_subagent_ms"):
                lines.append(_t("ui.stats_longest_subagent", locale,
                                duration=_format_duration(stats.get("longest_subagent_ms", 0))))
            if stats.get("tool_interrupts"):
                lines.append(_t("ui.stats_interrupts", locale, count=stats.get("tool_interrupts", 0)))
            if stats.get("tool_blocks"):
                lines.append(_t("ui.stats_blocks", locale, count=stats.get("tool_blocks", 0)))
            if stats.get("max_retry_depth"):
                lines.append(_t("ui.stats_max_retry_depth", locale,
                                count=stats.get("max_retry_depth", 0)))
            if stats.get("longest_message_words"):
                lines.append(_t("ui.stats_longest_message", locale, count=stats.get("longest_message_words", 0)))
            hooks_used = stats.get("hooks_used", set())
            if hooks_used:
                lines.append(_t("ui.stats_hooks_used", locale, count=len(hooks_used)))
            slash_used = stats.get("slash_commands_used", set())
            if slash_used:
                lines.append(_t("ui.stats_slash_commands", locale, count=len(slash_used)))
            platforms = stats.get("platforms", [])
            if isinstance(platforms, set):
                platforms = sorted(platforms)
            if platforms:
                lines.append(_t("ui.stats_platforms", locale, platforms=", ".join(platforms)))
            models = stats.get("models_used", [])
            if isinstance(models, set):
                models = sorted(models)
            if models:
                more = _t("ui.model_more", locale, count=len(models)-3) if len(models) > 3 else ""
                lines.append(_t("ui.stats_models", locale, models=", ".join(models[:3]), more=more))
            providers = stats.get("providers_used", [])
            if isinstance(providers, set):
                providers = sorted(providers)
            if providers:
                more = _t("ui.model_more", locale, count=len(providers)-3) if len(providers) > 3 else ""
                lines.append(_t("ui.stats_providers", locale, providers=", ".join(providers[:3]), more=more))
        lines.append(_t("ui.stats_footer", locale))
        if pct >= 100:
            lines.append(_t("ui.completionist_unlocked", locale))
        return "\n".join(lines)

    # Group filter
    for g in GROUPS:
        slug = g.lower().replace(" & ", " ").replace(" ", "_")
        if args == slug or args == g.lower().replace(" & ", "_"):
            group_key = g.lower().replace(" & ", "_").replace(" ", "_")
            group_name = _t(f"group.{group_key}", locale)
            lines = [_t("ui.group_filter_header", locale, emoji=GROUP_EMOJIS.get(g, "🎮"), group=group_name) + "\n"]
            for a_id, a_def in ACHIEVEMENT_DEFS.items():
                if a_def.get("group") == g:
                    lines.append(_format_badge(a_id, a_def, state, compact=True))
            return "\n".join(lines)

    # Default: compact group-summary view (full badge lists stay available
    # via `/achievements <group>` — keeps output under Discord's 2000-char cap)
    lines = [
        _t("ui.all_title", locale),
        _t("ui.all_subtitle", locale) + "\n",
    ]
    if newly:
        lines.append(_t("ui.recent_unlocked_section", locale))
        for a_id in newly[-3:]:
            a_def = ACHIEVEMENT_DEFS.get(a_id)
            if a_def:
                ach_name = _t(f"achievement.{a_id}.name", locale)
                ach_desc = _t(f"achievement.{a_id}.description", locale)
                lines.append(_t("ui.newly_prefix", locale) + f"**{ach_name}** — {ach_desc}")
        lines.append("")
    for group in GROUPS:
        ga = [(a_id, a_def) for a_id, a_def in ACHIEVEMENT_DEFS.items()
              if a_def.get("group") == group]
        if not ga:
            continue
        ug = sum(1 for a_id, _ in ga if a_id in unlocked_ids)
        group_key = group.lower().replace(" & ", "_").replace(" ", "_")
        group_name = _t(f"group.{group_key}", locale)
        bar = _progress_bar(ug, len(ga), width=10)
        lines.append(
            f"{GROUP_EMOJIS.get(group, '🎮')} **{group_name}** ({ug}/{len(ga)}) {bar}"
        )
    # Closest-to-unlock teaser (one line; full list via `/achievements next`)
    next_line = _next_up_hint(state)
    if next_line:
        lines.append("")
        lines.append(next_line)
    lines.append("")
    lines.append(_t("ui.summary_hint", locale))
    help_all = _t("ui.help_all", locale)
    help_filter = _t("ui.help_filter", locale)
    help_overview = _t("ui.help_overview", locale)
    help_latest = _t("ui.help_latest", locale)
    help_detail = _t("ui.help_detail", locale)
    help_next = _t("ui.help_next", locale)
    lines.append(_t("ui.help_footer", locale, all=help_all, filter=help_filter,
                     overview=help_overview, latest=help_latest, detail=help_detail,
                     next_up=help_next))
    return "\n".join(lines)


def _handle_achievement_detail(raw_args: str) -> str:
    a_id = raw_args.strip()
    if not a_id:
        return "Usage: `/achievement <id>`"
    a_def = ACHIEVEMENT_DEFS.get(a_id)
    if not a_def:
        matches = [aid for aid in ACHIEVEMENT_DEFS
                   if a_id.lower() in aid.lower() or a_id.lower() in ACHIEVEMENT_DEFS[aid]["name"].lower()]
        if len(matches) == 1:
            a_id = matches[0]
            a_def = ACHIEVEMENT_DEFS[a_id]
        elif len(matches) >= 1:
            # Cap the ambiguity list — a generic query can match dozens of
            # names and blow Discord's 2000-char message cap.
            shown = matches[:10]
            more = f" and {len(matches) - len(shown)} more" if len(matches) > len(shown) else ""
            return f"Multiple: {', '.join(shown)}{more}. Be more specific."
        else:
            return f"Unknown `{a_id}`. Use `/achievements`."

    state = _load_state()
    locale = state.get("locale", "en")
    a_state = state.get("achievements", {}).get(a_id, {})
    unlocked = a_state.get("unlocked", False)
    unlocked_at = a_state.get("unlocked_at")
    progress = a_state.get("progress")
    secret = a_def.get("secret", False) or a_def.get("hidden", False)
    if secret and not unlocked:
        name_display = "???"
        desc_str = "???"
        progress = None  # don't leak progress toward a locked secret
    else:
        name_display = _t(f"achievement.{a_id}.name", locale)
        desc_str = _t(f"achievement.{a_id}.description", locale)
    rarity_str = _t(f"rarity.{a_def['rarity']}", locale)
    group_key = a_def["group"].lower().replace(" & ", "_").replace(" ", "_")
    group_str = _t(f"group.{group_key}", locale)
    rarity_emoji = RARITY_EMOJIS.get(a_def['rarity'], '⬜')

    status_icon = "✅" if unlocked else "⬜"
    header = f"{status_icon} **{a_def['emoji']} {name_display}**"
    rarity_line = _t("ui.detail_rarity", locale, emoji=rarity_emoji, rarity=rarity_str)
    group_line = _t("ui.detail_group", locale, group=group_str)

    if unlocked:
        status_line = _t("ui.detail_unlocked_at", locale, time=unlocked_at or "?")
    elif progress:
        cur, tgt = progress["current"], progress["target"]
        pct = int(cur / tgt * 100) if tgt else 0
        bar = _progress_bar(cur, tgt)
        status_line = _t("ui.detail_progress", locale, bar=bar, current=cur, target=tgt, percent=pct)
    else:
        status_line = _t("ui.detail_status_locked", locale)

    return f"{header}\n*{desc_str}*\n\n{rarity_line}\n{group_line}\n{status_line}"


# ── Language Command Handler ──────────────────────────────────────────

LANGS = {"en": "English", "es": "Español", "fr": "Français", "pt": "Português"}
LANG_ALIASES = {
    "en": ("en", "english"), "es": ("es", "spanish", "español", "espanol"),
    "fr": ("fr", "french", "français", "francais"),
    "pt": ("pt", "portuguese", "português", "portugues", "portugais"),
}


def _handle_lang(raw_args: str) -> str:
    """Set or show language. Usage: /achievements lang <code>"""
    state = _load_state()
    args = raw_args.strip().lower()
    if not args:
        cur = state.get("locale", "en")
        return _t("ui.lang_set", cur, lang=LANGS.get(cur, cur), native=LANGS.get(cur, cur))

    target = None
    for code, aliases in LANG_ALIASES.items():
        if args == code or args in aliases:
            target = code
            break
    if not target:
        cur = state.get("locale", "en")
        return _t("ui.lang_invalid", cur, code=args)

    state["locale"] = target
    _save_state()
    return _t("ui.lang_set", target, lang=LANGS.get(target, target), native=LANGS.get(target, target))


def _synchronized(fn):
    """Serialize state-mutating hook/command bodies under _state_lock.

    The gateway executes parallel tool calls on worker threads
    (``execute_tool_calls_concurrent`` → ``propagate_context_to_thread``),
    so hooks can fire concurrently on different threads. The shared
    in-memory ``_state`` dict must never be mutated concurrently:
    read-modify-write counters (``tools_used[x] = tools_used[x] + 1``)
    would lose updates and check-then-act unlock sequences would race.
    ``_state_lock`` is an RLock because ``_save_state``/``_load_state``
    re-acquire it inside the body.
    """
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        with _state_lock:
            return fn(*args, **kwargs)
    return _wrapped


def register(ctx) -> None:
    """Plugin entry point — registers slash commands and hooks."""
    ctx.register_command("achievements", handler=_synchronized(_handle_achievements),
        description="View Hermes achievement progress and stats.",
        args_hint="[recent|next|stats|<group>|lang <code>]")
    ctx.register_command("achievement", handler=_synchronized(_handle_achievement_detail),
        description="Show details for a specific achievement.",
        args_hint="<achievement-id>")

    # Detection: pre_llm_call counts fresh conversations (is_first_turn)
    ctx.register_hook("pre_llm_call", _synchronized(_pre_llm_call))
    # Detection: pre_tool_call counts single-response tool batching
    ctx.register_hook("pre_tool_call", _synchronized(_pre_tool_call))
    # Observation: raw pre-truncation terminal output, env diversity, exit codes
    # (TRANSFORM hook — _transform_terminal_output always returns None)
    ctx.register_hook("transform_terminal_output", _synchronized(_transform_terminal_output))
    # Observation: full tool-result size / context bloat
    # (TRANSFORM hook — _transform_tool_result always returns None)
    ctx.register_hook("transform_tool_result", _synchronized(_transform_tool_result))
    # Detection: post_llm_call has conversation_history → tool calls
    ctx.register_hook("post_llm_call", _synchronized(_post_llm_call))
    ctx.register_hook("post_api_request", _synchronized(_post_api_request))
    # Preflight: local-endpoint usage + single-request input-token spikes
    ctx.register_hook("pre_api_request", _synchronized(_pre_api_request))
    # Per-tool detection with full arguments (cron jobs, delegation, files)
    ctx.register_hook("post_tool_call", _synchronized(_post_tool_call))
    # Session accounting: new-session counter for session milestones
    ctx.register_hook("on_session_start", _synchronized(_on_session_start))
    # Fallback metadata tracking + streaks
    ctx.register_hook("on_session_end", _synchronized(_on_session_end))
    # Subagent delegation: per-child counting (army_commander by children),
    # orchestrator role usage, failure resilience
    ctx.register_hook("subagent_stop", _synchronized(_on_subagent_stop))
    # Subagent spawn: true concurrency tracking (Conductor)
    ctx.register_hook("subagent_start", _synchronized(_on_subagent_start))
    # Approval decisions: permanent trust (yolo/trust_fall), denials
    ctx.register_hook("post_approval_response", _synchronized(_on_approval_response))
    # Approval gates: how often commands trigger approval prompts
    ctx.register_hook("pre_approval_request", _synchronized(_on_approval_request))
    # Fresh-session rotations (/new, /reset)
    ctx.register_hook("on_session_reset", _synchronized(_on_session_reset))
    # Shutdown/session-expiry flush: pending state + queued notifications
    ctx.register_hook("on_session_finalize", _synchronized(_on_session_finalize))
    # LLM API resilience: survived provider errors (Indestructible)
    ctx.register_hook("api_request_error", _synchronized(_on_api_request_error))
    # Multi-user messaging: distinct senders seen by the gateway
    ctx.register_hook("pre_gateway_dispatch", _synchronized(_on_pre_gateway_dispatch))