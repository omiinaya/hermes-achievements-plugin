# Changelog

## [2.5.0] — 2026-07-31

### Added

- **13th plugin hook: `post_api_request`** — fires once per successful provider API request and carries normalized `usage` token buckets plus `api_duration` in seconds. Opens a genuinely new observation dimension — token consumption and latency — previously invisible to the plugin (message counts were tracked, tokens were not).
- **4 new achievements (100 → 104, all in Milestones):** Token Tyro 💧 (100K total tokens), Token Wizard 🧙 (1M), Token Whale 🐋 (10M), Speed Demon ⚡ (25 API responses under 2s). The `usage` shape is provider-normalized by the gateway; the handler also falls back to `prompt_tokens + completion_tokens` when `total_tokens` is absent.
- **Stats view now shows `Tokens consumed: N`** (`ui.stats_tokens`, all 4 locales) — the first cost-oriented stat.
- Full-grind simulation extended to drive `_post_api_request` (1050 requests, mixed usage shapes, alternating fast/slow durations) — the all-105-unlockable invariant now covers the new dimension end-to-end.
- **Health check `--gateway` mode + hook kwarg contract check** — cross-verifies every `kwargs.get("...")` key the plugin reads against the kwargs the installed Hermes source actually passes to each hook. Catches the silent-no-op failure class: if a Hermes update renames a hook kwarg, achievements would quietly stop firing with green tests. Verified 17 keys across 7 hooks all delivered; the check is wired into `setup.sh --test` and covered by a CI test (skips gracefully where Hermes source is absent).
- **Trial and Error 🔬 (105th achievement)** — the gateway passes `status` (ok/cancelled/block/error) and `error_type` to `post_tool_call`; the plugin previously ignored them. Now `status="error"` feeds a `tool_errors` counter — 25 failed tool calls unlock Trial and Error (Expert group). The full-grind simulation drives 30 error-status calls so the all-105-unlockable invariant covers the new dimension.

## [2.4.4] — 2026-07-31

### Added

- **Full-grind simulation test** (`TestEveryAchievementUnlockable`) — drives all 12 hooks with escalating synthetic gateway data (1050 turns, 1128 tool calls, streaks, subagents, approvals, API errors, distinct users) and asserts **all 100 defs unlock**, with Completionist as the 100th. This enforces the "every def must be detectable" invariant that was previously documentation-only — after 4 rounds of achievement swaps, a def with an impossible threshold or typo'd key now fails CI with its ID listed.
- **`scripts/check_plugin.py` health check** — one-shot integrity verification: module loads, manifest hooks ↔ `register()` hooks agree (no drift), exactly-100 defs, locale parity, no dead detection-map references, `--live` reconciles the real `state.json` (stale entries, preserved unlocks, rolling-backup note), `--manifest` loads through the real PluginManager. Wired into `setup.sh --test` and covered by a CI test.
- **Stats view now shows `Plugin hooks authored: N`** (`ui.stats_hooks_used`, all 4 locales) — Hook Master's counter (distinct hooks authored via `register_hook` in plugin files) was tracked but never surfaced.
- **CI ruff pin** (`ruff>=0.15.14,<0.17`) — the N999 incident (v2.4.2) was caused by a newer ruff major shipping a new default ruleset that broke CI while local ruff passed. Patch releases still land automatically.
- **CI coverage gate raised 90% → 95%** — the suite has held 99%+ across 3 Python versions; a lower floor would let a regression slip 9 points without CI noticing.

## [2.4.3] — 2026-07-31

### Fixed

- **Notification origin target skipped for non-Discord platforms** — `HERMES_SESSION_CHAT_ID` is used as the origin delivery channel, but WhatsApp/Telegram chat IDs aren't Discord snowflakes, so the plugin was POSTing to a bogus `discord.com/.../channels/<whatsapp-id>/messages` URL (wasted request + missed origin notification). Origin is now only used when it's all digits.
- **`/achievement` ambiguity list could exceed Discord's cap** — a generic query (e.g. "the") matched dozens of names and produced a >2000-char "Multiple:" line. Now capped at 10 with an "and N more" suffix + "Be more specific" hint.

### Added

- **12th plugin hook: `on_session_finalize`** — fires when the gateway shuts down an agent or a session's reset policy expires. It force-flushes the debounced `_save_state` (a write may still be pending inside the 2s window) and synchronously delivers any notifications still in the 3s debounce window. Nothing is lost when the process exits.
- `_save_state` no-ops when no state has been loaded yet (finalize can fire before the first hook).
- Python 3.13 added to the CI matrix and pyproject classifiers.
- **`recent` view could exceed Discord's 2000-char cap** — it rendered all of `newly_unlocked` (up to 20 entries); now bounded to the 10 most recent. New matrix test asserts every view × every locale stays under the cap with 50 unlocks + 20 new entries.

## [2.4.2] — 2026-07-31

### Fixed

- **Wheel shipped no code** — the pyproject `packages.find` include (`achievements*`) matched nothing because the plugin is a single `__init__.py` at the repo root. The built wheel contained only metadata. Now packaged via `py-modules = ["__init__"]` + `data-files` (plugin.yaml + locales), so the wheel is a complete, pip-installable plugin.
- **Pip-installed copy couldn't find locales** — `_load_locales` only checked `$HERMES_HOME/plugins/achievements/locales`. Added a `__file__`-relative fallback to the wheel data dir, so a pip-installed copy resolves all 4 locales (verified: install wheel into fresh venv → 4 locales, 100 achievements each).
- **CI lint red on hyphenated checkout dir** — ruff N999 flags `hermes-achievements-plugin/` as an invalid module name. Scoped per-file ignore for `__init__.py` in pyproject.toml. Local lint was green (working dir named `achievements`) which masked the failure — always check `gh run list` after push.
- **Discord 10-embed cap** — burst batches larger than 10 unlocks are now chunked into multiple messages (10 + remainder) instead of one oversized payload that Discord would reject with HTTP 400.

### Added

- Stats view now shows the `Distinct users seen` counter (`ui.stats_users_seen`, all 4 locales) alongside the other v2.4 counters.

## [2.4.1] — 2026-07-31

### Added

- **11th plugin hook: `pre_gateway_dispatch`** — fires once per incoming user-originated message (before auth). This is the ONLY hook that sees messages from *other* users; everything else fires for agent turns. Tracks distinct senders (platform-scoped identity) in `stats.users_seen`:
  - `Social Butterfly` (Community) — received messages from 3 different users
  - `Party Host` (Community) — received messages from 10 different users
- Bot senders and internal/system events are ignored; same user repeating doesn't inflate the count.

### Removed

- 2 niche CLI-pattern achievements (locked for the primary user): `Name That Session` (`/title`) and `Help Seeker` (`--help`), with their `TERMINAL_PATTERNS` entries.

### Changed

- Group distribution (keeps exactly 100): Getting Started 12→10, Community 4→6.

### Fixed

- `scripts/update_locales.py` — NEW translations are now authoritative: stale English fallbacks left by a previous run are overwritten instead of skipped.

## [2.4.0] — 2026-07-31

### Added

- **Batched Discord notifications** — burst unlocks (several thresholds crossing in one turn) are now debounced into a single message with multiple embeds instead of one message per achievement. A 3-second window coalesces rapid unlocks; multi-unlock messages carry a `🎉 N achievements unlocked!` header. Single unlocks behave exactly as before.
- **Three new plugin hooks** (10 total), each mapping to a real Hermes gateway signal:
  - **`subagent_start`** — fires when a subagent is spawned (has `child_role`, `child_goal`). Paired with `subagent_stop`, it tracks TRUE concurrency: a live counter incremented on spawn and decremented on stop, with a persisted peak.
    - `Conductor` — ran 3 subagents simultaneously (peak concurrency, not just call count)
  - **`pre_approval_request`** — fires when an approval prompt is raised, before the user answers (has `command`, `surface`). Counts approval gates independently of how the user responds.
    - `Under Scrutiny` — triggered 10 approval requests
  - **`api_request_error`** — fires when an LLM provider call fails (invalid response, rate limit, timeout, retries exhausted; has `error_type`, `status_code`, `retry_count`). Rewards resilience.
    - `Indestructible` — survived 10 LLM API errors without quitting
- **Stats view** now surfaces the new counters: `Approvals requested`, `Peak concurrent agents`, `LLM API errors survived`.
- **`scripts/update_locales.py`** is now fully automated — it reads `ACHIEVEMENT_DEFS` from `__init__.py` as the source of truth, prunes dead keys, and backfills missing translations (falling back to English with a WARN if a translation is missing).

### Removed

- 3 niche, rarely-unlockable achievements (all were locked for the primary user): `Version Spotter` (Getting Started), `Plugin Browser` and `Skill Browser` (Community), with their `TERMINAL_PATTERNS` entries.

### Changed

- Group distribution (keeps exactly 100): Getting Started 13→12, Power User 22→23, Expert 16→18, Community 6→4.

## [2.3.1] — 2026-07-31

### Added

- **Secret achievements** — 6 achievements (Fresh Start, Cautious, Quick Draw, Orchestrator, Trust Fall, Resilient) are now marked `secret: true`. Locked secrets show `❓ ???` in group views, `???` in detail, and never appear in `/achievements next` (no progress leak). They reveal normally once unlocked. The support code existed since v2.0 — now it's actually used.

### Fixed

- **Locked secret names/descriptions leaked** — `_format_badge` showed the real name, and `_handle_achievement_detail` showed the real description, for locked secrets. Both now mask to `???`.
- **`/achievements next` leaked secret progress** — locked secrets are excluded from the closest-to-unlock list.
- **Dead notification branch removed** — `if not targets and origin_channel` was unreachable (targets is non-empty whenever origin_channel is set).
- **State hygiene** — stale achievement entries (removed/renamed across versions) are pruned from `state.json` on load.

## [2.3.0] — 2026-07-31

### Added

- **Three new plugin hooks** (7 total) with real gateway signals:
  - **`subagent_stop`** — fires once per `delegate_task` child with `child_role`, `child_status`, `duration_ms`. This is the authoritative subagent count: a single call with 3 tasks spawns 3 children. Drives:
    - `Army Commander` — now counts **children** (25 spawned), not `delegate_task` calls
    - `Orchestrator` — used an orchestrator-role subagent
    - `Resilient` — completed a task after a subagent failed/interrupted
  - **`post_approval_response`** — fires after the user answers an approval prompt (`choice`: once/session/always/deny/timeout):
    - `Trust Fall` — approved a command permanently ("always")
    - `Cautious` — denied an approval request
    - `YOLO Mode` / `YOLO Champion` — choosing "always" is the real-world equivalent of `--yolo` (the command never prompts again); each counts toward the 25-task champion
  - **`on_session_reset`** — fires when the gateway swaps in a fresh session key (`/new`, `/reset`):
    - `Fresh Start` — started a fresh session
    - `session_resets` counter in stats
- **5 new hook-backed achievements** (keeps exactly 100): Trust Fall, Cautious, Orchestrator, Resilient, Fresh Start
- **`scripts/update_locales.py`** — keeps all 4 locale files in sync when swapping achievements

### Removed

- 5 niche CLI-pattern achievements that were near-impossible to unlock from the gateway: `Star Gazer`, `Updater`, `Feedback Friend`, `Helpful Soul`, `Theme Setter` (with their `TERMINAL_PATTERNS` entries)

### Fixed

- **Army Commander undercounted parallel delegation** — counted `delegate_task` calls, but a call with 3 tasks spawns 3 subagents; now counts actual children via `subagent_stop`
- **YOLO Mode was CLI-only** — `--yolo` flag sniffing never fired on the gateway; approval "always" is now the primary signal (CLI flag still works)

## [2.2.1] — 2026-07-31

### Added

- **Discord notifications as rarity-colored embeds** — gray/green/blue/purple/gold card per rarity instead of plain text.
- **`/achievements next`** — shows the 3 achievements closest to unlocking with progress bars (all 4 locales, help footer updated).
- **State-file safety** — rolling `state.json.bak` before each save; corrupted state recovers from the backup instead of resetting to zero.
- **CI** (`.github/workflows/test.yml`) — pytest on Python 3.11/3.12 + README-sync gate. **AGENTS.md** repo guide.

### Fixed

- **Model/platform diversity regressed on gateway restart** — `Model Hopper`, `Multi-Model`, `Model Collector`, `Cross-Platform Operative/Networker/Veteran`, and `Gateway Guru` read from in-memory sets that reset on restart; now read from persisted `stats.models_used` / `stats.platforms`. Progress survives restarts.
- **`/achievements recent` arbitrary ordering** — falls back to `unlocked_at`-sorted top 3 when the recent-unlocks list is empty.
- **Default `/achievements` view exceeded Discord's 2000-char cap** (6120 chars with all 100 badges) — now a compact group-summary with progress bars; full badge lists stay one command away (`/achievements <group>`).
- **Stats view** — now shows live session summary (calls + distinct tool types) and tier counters (cron jobs, skills created, config changes) in all 4 locales.
- **Memory Keeper** only counts `memory` add/replace — `remove` no longer triggers it.
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
