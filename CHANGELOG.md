# Changelog

## [2.2.1] — 2026-07-31

### Fixed

- **Model/platform diversity regressed on gateway restart** — `Model Hopper`, `Multi-Model`, `Model Collector`, `Cross-Platform Operative/Networker/Veteran`, and `Gateway Guru` read from in-memory sets that reset on restart; now read from persisted `stats.models_used` / `stats.platforms`. Progress survives restarts.
- **`/achievements recent` arbitrary ordering** — falls back to `unlocked_at`-sorted top 3 when the recent-unlocks list is empty.
- **Stats view** — now shows live session summary (calls + distinct tool types) and tier counters (cron jobs, skills created, config changes) in all 4 locales.
- Internal: renamed `_SESSION_CATEGORY_THRESHOLDS` → `_SESSION_TOOL_THRESHOLDS` (it counts tool names, not categories).

## [2.2.0] — 2026-07-31

### Added

- **`post_tool_call` hook** — per-tool detection with full tool arguments (the primary detection path, replacing conversation-history scanning)
- **Argument-based achievements**, previously impossible to detect:
  - `Chain Reaction` — cron job chained via `context_from`
  - `Parallel Master` — `delegate_task` with 3+ tasks in the `tasks` array
  - `Precision Scheduler` — ISO timestamp schedule or `repeat="once"`
  - `Environment Tuner` — cron job with custom `workdir`/`env_file`
  - `Plugin Developer` — writing a file under a `/plugins/` path or named `plugin.yaml`
  - `Hook Master` — plugin code registering 3+ distinct hook types
- **Tiered counter achievements** via `_check_counter_achievements()`:
  - `Config Guru` now requires 15 real config changes (was: any `hermes config` mention)
  - `Plugin Pack` (5 plugins enabled), `Profile Collector` (5 profiles),
    `MCP Networker` (3 servers), `Skill Collector/Apprentice/Master` (skill installs),
    `YOLO Champion` (25 `--yolo` tasks)
- **`on_session_start` hook** — counts distinct sessions (fixes Persistent/session milestones)
- **Quick Draw** — 5 consecutive tool calls under 20s
- **Functional test suite** (`tests/test_detection.py`, 31 tests) — drives the hooks with synthetic gateway kwargs and verifies actual unlocks
- **README renderer** (`scripts/render_readme.py`) — regenerates achievement tables from `ACHIEVEMENT_DEFS` so docs can't drift

### Fixed

- **Message thresholds never unlocked** — `total_turns` was updated via `max(int(turn_id))` but Hermes passes `turn_id` as a string (`session:task:hex`), so Chatty/Century/Talkative/Legendary Chatter could never unlock. Now counted per `post_llm_call` firing (once per turn).
- **Session achievements used cumulative totals** — Power Session / 90-Turn Club / Ultra Marathon / Marathon Session were checked against all-time tool totals instead of single-session counts. Now tracked per-session via `active_session`.
- **Jack of All Trades / Workflow Builder used per-turn tools** — now accumulate distinct tool types across the whole session (and count tool *names*, not collapsed categories).
- **Skill Author unlocked on every `skill_manage` call** — now only on `action=create`/`edit`.
- **Cron Commander unlocked on any cronjob call** — now only on `action=create`.
- **Discord notification blocked the agent loop** — delivery is now a daemon thread (async, non-blocking).
- **`newly_unlocked` list unbounded** — capped at most recent 20.
- **README/defs drift** — 12 achievement names were missing from the README and 18 stale names were listed (e.g. "Multi-Tasker", "Session Master" never existed in code). Tables now generated from source.
- **State save storm** — `_save_state()` debounced to at most once per 2s (forced at turn/session boundaries).

## [2.1.0] — 2026-07-13

### Added

- **i18n support** for 4 languages: 🇪🇸 Spanish, 🇫🇷 French, 🇧🇷 Portuguese
- **`/achievements lang <code>`** — switch language at runtime (en/es/fr/pt)
- `_t()` translation function with locale auto-detection from state
- Locale files in `locales/` directory with all 100 achievements, groups, rarities, and UI strings
- All display strings are now locale-aware (commands, stats, detail view, notifications)

### Changed

- `plugin.yaml` → v2.1.0
- Discord notifications now respect the active locale
- Badge formatting, group headers, stats view all use translated strings

## [2.0.0] — 2026-07-12

### Added

- **100 achievements** across 6 groups (up from 26)
- **Community group** (11 achievements) — Star Gazer, Plugin Browser, Skill Browser, Theme Setter, Documentarian, Updater, Changelog Checker, Feedback Friend, Release Reader, Issue Tracker, Community Member
- **Tiered mastery** for every tool type — bronze/silver/gold progression (Terminal Jockey → Shell Master, File Whisperer → File Artisan, etc.)
- **Milestone chains** — Talkative (500/1K messages), Tool Fan/Addict/Obsessed (100/500/1K calls)
- **Streak system** — Week Warrior (7 days), Monthly Master (30 days)
- **Group & rarity collectors** — unlock all Common, Uncommon, Rare, Epic achievements
- **Dual-channel Discord delivery** — sends to home channel + origin channel, dedup when same
- **`.env`-aware token loading** — reads `DISCORD_BOT_TOKEN` from `~/.hermes/.env` instead of requiring it in environment

### Changed

- All detection is now inline via `post_llm_call` — no separate tracker script needed
- Notifications are standalone Discord messages (no inline response text pollution)

### Fixed

- Discord delivery was silently failing because `os.environ` didn't have the bot token — now reads directly from `.env`

## [1.2.0] — 2026-07-11

### Added

- Discord notification delivery — achievements post as standalone messages
- Slash command: `/achievements <group>` — filter by group

### Changed

- Removed `transform_llm_output` hook — no more inline response notifications

## [1.1.0] — 2026-07-11

### Added

- Tool Collector, Ghost in the Shell, Web Walker, Visionary, Memory Keeper, Code Wizard, Skill Finder, Skill Author
- Cron Commander, MCP Master, Agent Swarm, YOLO Mode, Session Sage, Profile Juggler
- Gateway Guru, Plugin Power, Cross-Platform Operative, The 90-Turn Club
- Early Bird, Century Mark
- `/achievements` and `/achievement` slash commands

## [1.0.0] — 2026-07-10

### Added

- Initial plugin scaffold with 5 Getting Started achievements
- `post_llm_call` hook for detection
- `transform_llm_output` hook for inline notifications
- State persistence via JSON
