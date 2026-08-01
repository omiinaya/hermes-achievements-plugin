"""
Hermes Achievements Plugin
===========================
Steam-style achievement badges for using and learning about Hermes Agent.
107 achievements across 6 categories.

Hooks:
  - post_llm_call:  detects tool calls from conversation_history → unlocks achievements
                    sends immediate Discord notification on unlock
  - on_session_end: tracks session metadata, streaks, and session-based achievements

Achievement notifications are delivered as standalone Discord messages the
moment they unlock — not appended to the next assistant response.
"""

import json
import os
import re
import shutil
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


def _send_discord_notification(ach_def):
    """Deliver achievement notification asynchronously (non-blocking).

    Unlocks are debounced: rapid-fire unlocks within the debounce window
    coalesce into ONE batched message (multiple embeds) rather than one
    message per achievement. A daemon timer does the delivery so a slow
    Discord API response never stalls the agent's hook pipeline.
    """
    global _notif_timer
    with _NOTIF_QUEUE_LOCK:
        _NOTIF_QUEUE.append(ach_def)
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

    # Origin channel where the achievement was unlocked (set by gateway)
    origin_channel = os.environ.get("HERMES_SESSION_CHAT_ID", "")

    state = _load_state()
    locale = state.get("locale", "en") if state else "en"

    embeds = []
    for ach_def in batch:
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

    # Build unique target set — dedup home vs origin. Origin is only used
    # when it looks like a Discord channel (numeric snowflake); other
    # platforms (WhatsApp chat IDs, Telegram IDs) would POST to a bogus
    # URL and fail.
    targets = []
    if home_channel:
        targets.append(("home", home_channel, home_thread or None))
    if origin_channel and origin_channel != home_channel and str(origin_channel).isdigit():
        targets.append(("origin", origin_channel, None))

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
    _send_discord_notification_batch([ach_def])


# ── State management (thread-safe, cached in memory) ────────────────────

_state_lock = threading.Lock()
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
            with open(_STATE_PATH, "w") as f:
                json.dump(state_copy, f, indent=2, default=str)
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
    for key in ("platforms", "models_used", "providers_used", "slash_commands_used", "hooks_used", "users_seen"):
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
# (the normal install), (2) data-files shipped inside the wheel next to
# this module (pip-installed copy). Falls back to English-only when
# neither exists (defensive; _t then returns raw keys).
_LOCALES_DIR = os.path.join(_HERMES_HOME, "plugins", "achievements", "locales")
_WHEEL_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "achievements", "locales")
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


# ── Achievement Definitions (100 total) ──────────────────────────────────

ACHIEVEMENT_DEFS = {
    # ═══════════════════════════════════════════════════════════════════════
    # 🚀 GETTING STARTED  (10)
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
    # ⚡ POWER USER  (23)
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

    # ═══════════════════════════════════════════════════════════════════════
    # 👑 EXPERT  (19)
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

    # ── Tool-error resilience (status from gateway: ok/cancelled/block/error)
    status = kwargs.get("status", "ok")
    if status == "error":
        stats["tool_errors"] = stats.get("tool_errors", 0) + 1
        if stats["tool_errors"] >= 25:
            _unlock("trial_and_error", now)
        else:
            _set_progress("trial_and_error", stats["tool_errors"], 25)

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


def _check_counter_achievements(stats, now):
    """Tiered achievements backed by stats counters."""
    cc = stats.get("config_changes", 0)
    if cc >= 15:
        _unlock("config_guru", now)
    else:
        _set_progress("config_guru", cc, 15)
    pe = stats.get("plugins_enabled", 0)
    if pe >= 5:
        _unlock("plugin_pack", now)
    else:
        _set_progress("plugin_pack", pe, 5)
    pc = stats.get("profiles_created", 0)
    if pc >= 5:
        _unlock("profile_collector", now)
    else:
        _set_progress("profile_collector", pc, 5)
    mc = stats.get("mcp_servers_connected", 0)
    if mc >= 3:
        _unlock("mcp_networker", now)
    else:
        _set_progress("mcp_networker", mc, 3)
    si = stats.get("skills_installed", 0)
    if si >= 1:
        _unlock("skill_finder", now)
    else:
        _set_progress("skill_finder", si, 1)
    if si >= 5:
        _unlock("skill_apprentice", now)
    else:
        _set_progress("skill_apprentice", si, 5)
    if si >= 15:
        _unlock("skill_master", now)
    else:
        _set_progress("skill_master", si, 15)
    yt = stats.get("yolo_tasks", 0)
    if yt >= 25:
        _unlock("yolo_champion", now)
    else:
        _set_progress("yolo_champion", yt, 25)
    cj = stats.get("cron_jobs_created", 0)
    if cj >= 5:
        _unlock("cron_master", now)
    else:
        _set_progress("cron_master", cj, 5)
    if cj >= 15:
        _unlock("cron_overlord", now)
    else:
        _set_progress("cron_overlord", cj, 15)
    sr = stats.get("session_resumes", 0)
    if sr >= 10:
        _unlock("session_surfer", now)
    else:
        _set_progress("session_surfer", sr, 10)


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
                    if token.startswith("/") and len(token) > 1:
                        slash_cmds_this_turn.add(token.lower())
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

    _save_state()


# ── Hook: on_session_start ───────────────────────────────────────────────
# Fired once when a brand-new session is created (not on continuation).

def _on_session_start(**kwargs):
    """Count distinct sessions (powers the Persistent / session milestones)."""
    state = _load_state()
    stats = state.setdefault("stats", {})
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
    """Count LLM API errors the agent survived."""
    state = _load_state()
    stats = state.setdefault("stats", {})
    now = datetime.now(UTC).isoformat()

    stats["api_errors"] = stats.get("api_errors", 0) + 1
    errors = stats["api_errors"]

    if errors >= 10:
        _unlock("indestructible", now)
    else:
        _set_progress("indestructible", errors, 10)

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
# choice: "once" | "session" | "always" | "deny" | "timeout".
# "always" = permanent trust (real YOLO mode); "deny" = cautious.

def _on_approval_response(**kwargs):
    """Detect approval behavior: permanent trust, denial, yolo mode."""
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

    _check_group_completions()
    _check_completionist()
    _save_state()


# ── Slash Command Handlers ──────────────────────────────────────────────

def _progress_bar(current, target, width=10):
    if target <= 0:
        return "░" * width
    filled = min(int(current / target * width), width)
    return "█" * filled + "░" * (width - filled)


def _format_badge(a_id, a_def, state):
    a_state = state.get("achievements", {}).get(a_id, {})
    unlocked = a_state.get("unlocked", False)
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
        bar = _progress_bar(cur, tgt)
        prog_str = _t("ui.detail_progress", locale, bar=bar, current=cur, target=tgt, percent=int(cur/tgt*100))

    return _t("ui.badge_format", locale, icon=icon, name=name_str, description=desc_str, progress=prog_str)


def _handle_next_up(state) -> str:
    """Show the achievements closest to unlocking (by progress %)."""
    locale = state.get("locale", "en")
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
    if not candidates:
        return _t("ui.next_empty", locale)
    candidates.sort(reverse=True)
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
    ach_state = state.get("achievements", {})
    best = None
    for aid, a_def in ACHIEVEMENT_DEFS.items():
        s = ach_state.get(aid, {})
        if s.get("unlocked"):
            continue
        if a_def.get("secret", False) or a_def.get("hidden", False):
            continue
        prog = s.get("progress")
        if not prog or not prog.get("target"):
            continue
        cur = prog.get("current", 0)
        tgt = prog.get("target", 1)
        pct = cur / tgt
        if pct >= 1.0:
            continue
        if best is None or pct > best[0]:
            best = (pct, aid, cur, tgt)
    if best is None:
        return ""
    pct, aid, cur, tgt = best
    a_def = ACHIEVEMENT_DEFS[aid]
    name = _t(f"achievement.{aid}.name", locale)
    bar = _progress_bar(cur, tgt)
    pct_int = int(pct * 100)
    return _t("ui.next_hint", locale, name=name, bar=bar,
              current=cur, target=tgt, percent=pct_int)


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
            if stats.get("max_concurrent_subagents"):
                lines.append(_t("ui.stats_max_concurrent", locale, count=stats.get("max_concurrent_subagents", 0)))
            if stats.get("api_errors"):
                lines.append(_t("ui.stats_api_errors", locale, count=stats.get("api_errors", 0)))
            if stats.get("users_seen"):
                lines.append(_t("ui.stats_users_seen", locale, count=len(stats.get("users_seen", set()))))
            if stats.get("session_resets"):
                lines.append(_t("ui.stats_session_resets", locale, count=stats.get("session_resets", 0)))
            hooks_used = stats.get("hooks_used", set())
            if hooks_used:
                lines.append(_t("ui.stats_hooks_used", locale, count=len(hooks_used)))
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
                    lines.append(_format_badge(a_id, a_def, state))
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


def register(ctx) -> None:
    """Plugin entry point — registers slash commands and hooks."""
    ctx.register_command("achievements", handler=_handle_achievements,
        description="View Hermes achievement progress and stats.",
        args_hint="[recent|next|stats|<group>|lang <code>]")
    ctx.register_command("achievement", handler=_handle_achievement_detail,
        description="Show details for a specific achievement.",
        args_hint="<achievement-id>")

    # Detection: post_llm_call has conversation_history → tool calls
    ctx.register_hook("post_llm_call", _post_llm_call)
    ctx.register_hook("post_api_request", _post_api_request)
    # Per-tool detection with full arguments (cron jobs, delegation, files)
    ctx.register_hook("post_tool_call", _post_tool_call)
    # Session accounting: new-session counter for session milestones
    ctx.register_hook("on_session_start", _on_session_start)
    # Fallback metadata tracking + streaks
    ctx.register_hook("on_session_end", _on_session_end)
    # Subagent delegation: per-child counting (army_commander by children),
    # orchestrator role usage, failure resilience
    ctx.register_hook("subagent_stop", _on_subagent_stop)
    # Subagent spawn: true concurrency tracking (Conductor)
    ctx.register_hook("subagent_start", _on_subagent_start)
    # Approval decisions: permanent trust (yolo/trust_fall), denials
    ctx.register_hook("post_approval_response", _on_approval_response)
    # Approval gates: how often commands trigger approval prompts
    ctx.register_hook("pre_approval_request", _on_approval_request)
    # Fresh-session rotations (/new, /reset)
    ctx.register_hook("on_session_reset", _on_session_reset)
    # Shutdown/session-expiry flush: pending state + queued notifications
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    # LLM API resilience: survived provider errors (Indestructible)
    ctx.register_hook("api_request_error", _on_api_request_error)
    # Multi-user messaging: distinct senders seen by the gateway
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)