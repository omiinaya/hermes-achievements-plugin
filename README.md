# Hermes Achievements Plugin 🏆

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Hermes](https://img.shields.io/badge/hermes-agent-plugin-8B5CF6.svg)](https://hermes-agent.nousresearch.com)
[![CI](https://github.com/omiinaya/hermes-achievements-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/omiinaya/hermes-achievements-plugin/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen.svg)](https://github.com/omiinaya/hermes-achievements-plugin/actions/workflows/test.yml)

**100 Steam-style achievement badges** for [Hermes Agent](https://hermes-agent.nousresearch.com). Unlock achievements as you use Hermes — run commands, search the web, schedule cron jobs, create skills, and explore the platform. Achievements are tracked silently and delivered to your Discord home channel the moment they unlock.

## Quick Start

### Install

1. Copy the plugin files to your Hermes plugins directory:
   ```bash
   cp -r achievements ~/.hermes/plugins/
   ```
2. The plugin is auto-discovered on next gateway start. Enable it if needed:
   ```bash
   hermes plugins enable achievements
   ```
3. Restart the Hermes gateway:
   ```bash
   hermes gateway restart
   ```

### Requirements

- Hermes Agent 1.0+
- Python 3.11+
- Discord bot token in `~/.hermes/.env` (for notification delivery):
  ```
  DISCORD_BOT_TOKEN=your_token_here
  DISCORD_HOME_CHANNEL=your_home_channel_id
  ```

No external Python dependencies — the plugin uses only the standard library.

### Usage

```
/achievements              Group summary + recently unlocked (Discord-safe)
/achievements stats        Overall stats and unlock percentage
/achievements next         Closest achievements to unlocking
/achievements recent       Recently unlocked achievements
/achievements <group>      Filter by group name (full badge list)
/achievement <id>          Detail view with progress bar
```

When an achievement unlocks, a notification is posted to:
- Your Hermes **home channel** (configured via `DISCORD_HOME_CHANNEL`)
- The **channel where you're chatting** (if different from home)

Notifications are rarity-colored Discord embeds (gray → gold) posted asynchronously via a debounced timer — rapid unlock bursts are batched into a single message (capped at 10 embeds per Discord message), and they never block the agent loop.

### Example output

```
/achievements

**🎮 Hermes Achievements**
*Achievements unlock automatically as you use Hermes*

**🔥 Recently Unlocked:**
  ✅ **First Steps** — Send your first message to Hermes
  ✅ **Web Walker** — Search the web using Hermes

🚀 **Getting Started** (2/10) ██░░░░░░░░
🛠️ **Tools & Skills** (2/28) █░░░░░░░░░
⚡ **Power User** (0/23) ░░░░░░░░░░
👑 **Expert** (0/18) ░░░░░░░░░░
🎯 **Milestones** (0/15) ░░░░░░░░░░
🤝 **Community** (0/6) ░░░░░░░░░░

🔮 Closest to unlock: **Deep Diver** ████░░░░░░ 2/5 (40%)

/achievements next

🎯 **Next Up** — closest to unlocking:

🟩 **Deep Diver** — ████░░░░░░ 2/5 (40%)
🟦 **Config Guru** — █░░░░░░░░░ 2/15 (13%)
🟩 **Terminal Jockey** — █░░░░░░░░░ 3/25 (12%)
```

### Multi-Language Support (i18n) 🌐

The plugin supports 4 languages:

| Code | Language | Native Name |
|------|----------|-------------|
| `en` | English | English (default) |
| `es` | Spanish | Español |
| `fr` | French | Français |
| `pt` | Portuguese | Português |

Switch languages at any time:

```
/achievements lang es     → Switch to Spanish
/achievements lang fr     → Switch to French
/achievements lang pt     → Switch to Portuguese
/achievements lang        → Show current language
```

Achievement names, descriptions, group labels, rarity names, and all UI text (stats, badges, detail views, help text, Discord notifications) are translated. The language is persisted in `state.json` and stays across sessions.

Example — Spanish output:
```
/achievements lang es

✅ Cambiado a Español

/achievements recent

🔥 Logros Desbloqueados Recientemente
⬜ Fantasma en la Máquina — Ejecuta tu primer comando de terminal
```

### Secret Achievements 🕵️

Some achievements are **secret** — while locked they show `❓ ???` in group
views, `???` in their detail view, and are excluded from `/achievements next`
(no progress leak). They reveal their name and description only when you
unlock them, Steam-style. Currently secret: `Fresh Start`, `Cautious`,
`Quick Draw`, `Orchestrator`, `Trust Fall`, `Resilient`.

## Achievement Groups

### 🚀 Getting Started (10)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 👣 | First Steps | Send your first message to Hermes | Common |
| 🔧 | Config Tinkerer | Change a Hermes configuration setting | Common |
| 🏥 | Clean Bill of Health | Run `hermes doctor` to check system health | Common |
| 🎭 | Model Hopper | Switch to a different AI model | Common |
| 🗣️ | Chatty | Send 25 messages to Hermes | Common |
| 📋 | Slash Commander | Use 3 different slash commands | Common |
| 🔄 | Persistent | Send messages across 3 different sessions | Common |
| 🌱 | Fresh Start | Start a fresh session with /new or /reset | Common |
| 🌙 | Night Owl | Use Hermes after midnight (local time) | Uncommon |
| 🛡️ | Cautious | Deny an approval request | Uncommon |

### 🛠️ Tools & Skills (28)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 👻 | Ghost in the Shell | Run your first terminal command through Hermes | Common |
| 🌐 | Web Walker | Search the web using Hermes | Uncommon |
| 👁️ | Visionary | Analyze an image with Hermes | Uncommon |
| 🧠 | Skill Collector | Install a skill from the hub | Uncommon |
| 📖 | Memory Keeper | Save a fact to persistent memory | Uncommon |
| 🛠️ | Jack of All Trades | Use 5 different Hermes tool types in a single session | Uncommon |
| 🖥️ | Terminal Jockey | Run 25 terminal commands | Uncommon |
| 🔍 | Deep Diver | Perform 25 web searches | Uncommon |
| 🧠 | Skill Apprentice | Install 5 skills | Uncommon |
| 📁 | File Whisperer | Read or write 25 files | Uncommon |
| 🔍 | Session Detective | Search past sessions 10 times | Uncommon |
| 🧪 | Code Wizard | Execute 10 code blocks with execute_code | Rare |
| ✍️ | Skill Author | Create your own custom Hermes skill | Rare |
| 🖥️ | Shell Master | Run 100 terminal commands | Rare |
| 💻 | Code Slinger | Execute 50 code blocks | Rare |
| 🧠 | Skill Master | Install 15 skills | Rare |
| ✍️ | Skill Artisan | Create 5 skills | Rare |
| 📖📖 | Memory Archivist | Save 25 facts to memory | Rare |
| ⏰ | Cron Master | Have 5 active cron jobs | Rare |
| 🛠️ | Tool Hoarder | Use 10 different Hermes tool types cumulatively | Rare |
| 📁 | File Artisan | Read or write 100 files | Rare |
| 🔌 | MCP Networker | Connect 3 MCP servers | Rare |
| 💻 | Code Architect | Execute 100 code blocks | Epic |
| ✍️ | Skill Virtuoso | Create 15 skills | Epic |
| 📖📖📖 | Memory Librarian | Save 100 facts to memory | Epic |
| ⏰ | Cron Overlord | Have 15 active cron jobs | Epic |
| 🛠️🛠️ | Complete Toolset | Use every available Hermes tool type at least once | Epic |
| 👥 | Army Commander | Spawn 25 subagents with delegate_task | Epic |

### ⚡ Power User (23)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🔄 | Session Sage | Resume a past session with --continue or /resume | Uncommon |
| 🏄 | Session Surfer | Resume 10 different sessions | Uncommon |
| ⏰ | Cron Commander | Schedule your first cron job | Rare |
| 🔌 | MCP Master | Add an MCP server connection | Rare |
| 👥 | Agent Swarm | Spawn a subagent with delegate_task | Rare |
| 👤 | Profile Juggler | Create a named Hermes profile | Rare |
| 👤 | Profile Collector | Create 5 Hermes profiles | Rare |
| ⛓️ | Chain Reaction | Chain 2 cron jobs together with context_from | Rare |
| 🎭 | Multi-Model | Use 5 different AI models | Rare |
| 🏗️ | Workflow Builder | Use 8 different tool types in a single session | Rare |
| ⚡ | Quick Draw | Complete 5 tasks with rapid turnaround | Rare |
| ⚡⚡ | Parallel Master | Run 3 subagents in parallel with a single delegate_task | Rare |
| 🎻 | Conductor | Run 3 subagents simultaneously (peak concurrency) | Rare |
| 🎼 | Orchestrator | Use an orchestrator-role subagent | Rare |
| 🪂 | Trust Fall | Approve a command permanently with 'always' | Rare |
| 🔐 | YOLO Mode | Run with --yolo flag or disable approval prompts | Epic |
| 🔐 | YOLO Champion | Complete 25 tasks without approval prompts | Epic |
| 🌉 | Gateway Networker | Connect to 3 different messaging platforms | Epic |
| 🧩 | Plugin Pack | Have 5 plugins enabled | Epic |
| 🎭 | Model Collector | Use 10 different AI models | Epic |
| 🎯 | Tool Diversity | Use every available Hermes tool category | Epic |
| 🤖 | Marathon Session | Reach 200 tool calls in a single session | Legendary |
| 🧩 | Plugin Developer | Create your own Hermes plugin | Legendary |

### 👑 Expert (18)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🌍 | Multi-Lingual | Communicate with Hermes in a language other than English | Uncommon |
| 📚 | Doc Diver | Read the Hermes documentation | Uncommon |
| 🌉 | Gateway Guru | Connect Hermes to a messaging platform gateway | Rare |
| 🧩 | Plugin Power | Install and enable a Hermes plugin | Rare |
| 🔧 | Config Guru | Modify 15 different configuration settings | Rare |
| 🎯 | Precision Scheduler | Schedule a one-shot cron job for a specific time | Rare |
| ⚙️ | Environment Tuner | Configure custom environment variables for a cron job | Rare |
| 🔍 | Under Scrutiny | Trigger 10 approval requests | Rare |
| 🤖 | The 90-Turn Club | Reach 90 tool calls in a single session (default max_turns) | Epic |
| 📡 | Cross-Platform Operative | Chat with Hermes from 2+ different platforms | Epic |
| 🔌 | MCP Wizard | Write a custom MCP server configuration | Epic |
| 🔷 | Rare Collector | Unlock every Rare achievement | Epic |
| 🧗 | Resilient | Complete a task after a subagent failed | Epic |
| 🛡️ | Indestructible | Survive 10 LLM API errors without quitting | Epic |
| 📡📡 | Cross-Platform Veteran | Chat with Hermes from 5+ different platforms | Legendary |
| 🪝 | Hook Master | Create a plugin using 3+ different hook types | Legendary |
| 📈 | CLI Champion | Execute 500 terminal commands | Legendary |
| 💪 | Ultra Marathon | Reach 150 tool calls in a single session | Legendary |

### 🎯 Milestones (15)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🐦 | Early Bird | Use Hermes before 6 AM | Uncommon |
| 🔧 | Tool Fan | Accumulate 100 total tool calls | Uncommon |
| 🔧 | Tool Addict | Accumulate 500 total tool calls | Rare |
| 📅 | Week Warrior | Use Hermes 7 days in a row | Rare |
| 💪 | Power Session | Make 50 tool calls in a single session | Rare |
| 💯 | Century Mark | Accumulate 100+ messages across all sessions | Epic |
| 💬 | Talkative | Send 500 messages total | Epic |
| 🔧 | Tool Obsessed | Accumulate 1,000 total tool calls | Epic |
| 🚀 | Getting Started Complete | Unlock every Getting Started achievement | Epic |
| 🏆 | Completionist | Unlock every other achievement | Legendary |
| 💬💬 | Legendary Chatter | Send 1,000 messages total | Legendary |
| 📅📅 | Monthly Master | Use Hermes 30 days in a row | Legendary |
| 🟣 | Epic Collector | Unlock every Epic achievement | Legendary |
| 🛠️ | Tools Complete | Unlock every Tools & Skills achievement | Legendary |
| ⚡ | Power User Complete | Unlock every Power User achievement | Legendary |

### 🤝 Community (6)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 📋 | Changelog Checker | Read the Hermes changelog | Common |
| ⚙️ | First Config | View the Hermes configuration | Common |
| 📝 | Release Reader | Read the latest Hermes release notes | Uncommon |
| 🦋 | Social Butterfly | Receive messages from 3 different users | Uncommon |
| 🎉 | Party Host | Receive messages from 10 different users | Rare |
| 🤝 | Community Complete | Unlock every Community achievement | Epic |

## Architecture

Achievements are detected via twelve plugin hooks — no separate scanner or cron job needed:

1. **`post_tool_call`** fires after *every* tool execution with the full tool arguments. This is the primary detection path: per-tool usage counters, per-session tool tracking, and argument-based achievements (cron job chaining via `context_from`, parallel delegation via `tasks`, plugin/hook authoring via file content, skill creation).
2. **`post_llm_call`** fires once per turn and handles per-turn signals: cumulative message counts, model/platform diversity, user-command pattern matching (`hermes doctor`, `/title`, `--yolo`, ...), tiered command counters (config changes, plugins enabled, skills installed), and group/rarity completion checks.
3. **`on_session_start`** counts distinct sessions (drives the Persistent / session milestones).
4. **`on_session_end`** tracks daily streaks (Week Warrior, Monthly Master) and re-checks completions.
5. **`subagent_stop`** fires once per child agent after `delegate_task` finishes, with `child_role`, `child_status`, and `duration_ms`. This is the authoritative subagent count (a single call with 3 tasks spawns 3 children), driving Army Commander (25 children), Orchestrator (orchestrator role), and Resilient (failed/interrupted child).
6. **`subagent_start`** fires when a subagent is spawned. It increments a live concurrency counter that `subagent_stop` decrements — the peak (max simultaneous children) drives Conductor (3 concurrent subagents). This is true parallelism, not just call counting.
7. **`post_approval_response`** fires after the user responds to an approval prompt. Choosing *always* (permanent trust) unlocks Trust Fall and counts toward YOLO Mode / YOLO Champion; choosing *deny* unlocks Cautious.
8. **`pre_approval_request`** fires when an approval prompt is raised, before the user answers. It counts how often commands trigger approval gates — 10 gates unlock Under Scrutiny, independent of how the user responds.
9. **`on_session_reset`** fires when the gateway swaps in a fresh session key (`/new`, `/reset`) — drives Fresh Start and the session-resets counter.
10. **`api_request_error`** fires when an LLM provider call fails (invalid response, rate limit, timeout, retries exhausted). Surviving 10 such errors without quitting unlocks Indestructible.
11. **`pre_gateway_dispatch`** fires once per incoming user-originated message, before auth. It is the ONLY hook that sees messages from *other* users (everything else fires for agent turns) — distinct senders drive Social Butterfly (3 users) and Party Host (10 users).
12. **`on_session_finalize`** fires when the gateway shuts down an agent or a session's reset policy expires. It force-flushes the debounced state save and synchronously delivers any notifications still in the debounce window — nothing is lost when the process exits.

When an achievement unlocks, a Discord notification is posted asynchronously (debounced daemon timer — never blocks the agent loop) via the raw HTTP API to both the home channel and the channel where it was unlocked; bursts coalesce into one message.

### File layout

```
~/.hermes/plugins/achievements/
├── __init__.py        # Plugin code: hooks, definitions, detection, commands
├── plugin.yaml        # Plugin metadata (name, version, hooks)
├── pyproject.toml     # Python package metadata
├── scripts/
│   ├── render_readme.py   # Regenerates README achievement tables from defs
│   ├── update_locales.py  # Auto-syncs achievement keys across all 4 locales
│   ├── bump_version.py    # Bumps the version in pyproject/plugin.yaml/setup.sh
│   └── check_plugin.py    # Health check: defs, locales, hooks, live state
├── locales/           # i18n JSON files (en/es/fr/pt)
├── tests/
│   ├── test_plugin.py     # Static validation (defs, locales, files)
│   └── test_detection.py  # Functional hook-driven detection tests
├── CHANGELOG.md       # Version history
├── LICENSE            # MIT License
└── README.md          # This file
```

State data is stored at `~/.hermes/achievements/state.json` (user-local, not part of the repo).

## Development

```bash
# Edit the plugin
vim ~/.hermes/plugins/achievements/__init__.py

# Run the test suite (static + functional) — includes the full-grind
# simulation that proves all 100 achievements can unlock
python3 -m pytest tests/ -q

# Run the one-shot health check (defs, locales, manifest↔register hooks,
# live state) — add --manifest to also load through the real PluginManager
python3 scripts/check_plugin.py --live --manifest

# Changes take effect on gateway restart (kills MCP connections — get
# explicit user approval first)
hermes gateway restart

# View achievements
/achievements
```

### Adding a new achievement

1. Add an entry to `ACHIEVEMENT_DEFS` with a unique `id`, `name`, `emoji`, `description`, `rarity`, and `group`
2. Add detection logic — tool-count thresholds in `_TOOL_THRESHOLDS`, argument detection in `_check_tool_args()`, user-command patterns in `TERMINAL_PATTERNS`, or counter checks in `_check_counter_achievements()`
3. Add the translation keys to `locales/en.json` (and the other locales)
4. Regenerate the README tables:
   ```bash
   python3 scripts/render_readme.py
   ```
5. Run `python3 -m pytest tests/ -q` — the suite enforces 100 definitions, key parity across locales, and detection behavior; the full-grind test (`TestEveryAchievementUnlockable`) verifies the new def actually unlocks through a real hook call
6. Run `python3 scripts/check_plugin.py` — confirms defs, locales, and manifest↔register agreement in one shot

## License

MIT
