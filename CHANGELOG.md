# Changelog

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
