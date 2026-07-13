# Hermes Achievements Plugin 🏆

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Hermes](https://img.shields.io/badge/hermes-agent-plugin-8B5CF6.svg)](https://hermes-agent.nousresearch.com)

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
/achievements              View all achievements and progress
/achievements stats        Overall stats and unlock percentage
/achievements recent       Recently unlocked achievements
/achievements <group>      Filter by group name
/achievement <id>          Detail view with progress bar
```

When an achievement unlocks, a notification is posted to:
- Your Hermes **home channel** (configured via `DISCORD_HOME_CHANNEL`)
- The **channel where you're chatting** (if different from home)

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

## Achievement Groups

### 🚀 Getting Started (11)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 👣 | First Steps | Send your first message | Common |
| 🔧 | Config Tinkerer | Change a config setting | Common |
| 🏥 | Clean Bill of Health | Run `hermes doctor` | Common |
| 💬 | Name That Session | Name a session with /title | Common |
| 🎭 | Model Hopper | Switch to a different AI model | Common |
| 🗣️ | Chatty | Send 25 messages | Common |
| 🌙 | Night Owl | Use Hermes after midnight | Uncommon |
| 📋 | Slash Commander | Use 3 different slash commands | Common |
| 📖 | Help Seeker | Use --help on a command | Common |
| ℹ️ | Version Spotter | Check the Hermes version | Common |
| 🔄 | Persistent | Send messages across 3 sessions | Common |

### 🛠️ Tools & Skills (28)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🛠️ | Jack of All Trades | Use 5 different tool types in one session | Uncommon |
| 👻 | Ghost in the Shell | First terminal command | Common |
| 🖥️ | Terminal Jockey | 25 terminal commands | Common |
| 🖥️🖥️ | Shell Master | 100 terminal commands | Rare |
| 🌐 | Web Walker | First web search | Uncommon |
| 🔍 | Deep Diver | 25 web searches | Rare |
| 👁️ | Visionary | Analyze an image | Uncommon |
| 🧪 | Code Wizard | Execute 10 code blocks | Rare |
| 💻 | Code Slinger | Execute 50 code blocks | Rare |
| 💻💻 | Code Architect | Execute 100 code blocks | Epic |
| 🧠 | Skill Collector | Install first skill | Uncommon |
| 🧠🧠 | Skill Apprentice | Use skill_manage 5 times | Uncommon |
| 🧠🧠🧠 | Skill Master | Use skill_manage 30 times | Rare |
| ✍️ | Skill Author | Create first custom skill | Rare |
| ✍️✍️ | Skill Artisan | Create 15 skills | Rare |
| 📖 | Memory Keeper | First memory save | Uncommon |
| 📖📖 | Memory Archivist | 25 memory saves | Uncommon |
| 📖📖📖 | Memory Librarian | 100 memory saves | Rare |
| ⏰ | Cron Commander | First cron job | Rare |
| ⏰⏰ | Cron Master | 5 cron jobs | Rare |
| ⏰⏰⏰ | Cron Overlord | 15 cron jobs | Epic |
| 🛠️🛠️ | Tool Hoarder | Use 10 different tools | Rare |
| 🛠️🛠️🛠️ | Complete Toolset | Use 18 different tools | Epic |
| 📁 | File Whisperer | Read or write 25 files | Common |
| 📁📁 | File Artisan | Read or write 100 files | Uncommon |
| 🔌 | MCP Master | Connect first MCP server | Rare |
| 🔌🔌 | MCP Networker | Connect 3 MCP servers | Epic |
| 👥 | Agent Swarm | First delegate_task | Rare |
| 👥👥 | Army Commander | 25 delegate_task calls | Epic |
| 🔍 | Session Detective | 10 session searches | Uncommon |
| 🌐 | Browser Explorer | 10 browser tool actions | Uncommon |

### ⚡ Power User (20)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🔐 | YOLO Mode | Disable approval prompts | Epic |
| 🔐🔐 | YOLO Champion | Use --yolo 5+ times | Epic |
| 👤 | Profile Juggler | Create a profile | Rare |
| 👤👤 | Profile Collector | Create 5 profiles | Rare |
| 🤖 | The 90-Turn Club | 90 tool calls in one session | Epic |
| 🤖🤖 | Marathon Session | 200 tool calls in one session | Legendary |
| 💪 | Busy Bee | 20 tool calls in one session | Uncommon |
| 🌉 | Gateway Guru | Connect to a messaging platform | Rare |
| 🌉🌉 | Gateway Networker | Connect to 3 platforms | Epic |
| 🧩 | Plugin Power | Enable a plugin | Rare |
| 🧩🧩 | Plugin Developer | Enable plugins 3+ times | Rare |
| 🧩🧩🧩 | Plugin Pack | Have 5 plugins enabled | Epic |
| 🎭 | Multi-Model | Use 5 different models | Rare |
| 🎭🎭 | Model Collector | Use 10 different models | Epic |
| 🔄 | Session Sage | Resume a past session | Uncommon |
| 🏄 | Session Surfer | Resume 10 sessions | Rare |
| 🔧 | Config Explorer | Change 10 config settings | Uncommon |
| 👥👥👥 | Power Delegator | 50 delegate_task calls | Epic |
| 🎯 | Tool Diversity | Use tools from all categories | Rare |
| 📅 | Tenacious | Have 10+ sessions | Uncommon |

### 👑 Expert (15)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 📡📡 | Cross-Platform Veteran | Chat from 5+ platforms | Legendary |
| 🧩🧩🧩 | Hook Master | Use skill_manage 20 times | Legendary |
| 🔧🔧 | Config Guru | Change 15 config settings | Epic |
| 📈 | CLI Champion | 500 terminal commands | Legendary |
| 🔌🔌 | MCP Wizard | Connect 5 MCP servers | Legendary |
| 🎪 | Session Master | 500 total turns | Legendary |
| 🎯 | Precision Scheduler | Schedule a cron with a specific time | Rare |
| 🎯🎯 | Multi-Tasker | Use 5+ tools in one turn | Epic |
| 💪💪 | Ultra Marathon | 150 tool calls in one session | Legendary |
| 🌍 | Multi-Lingual | Communicate in a non-English language | Epic |
| 🏆 | Power User Champion | Unlock all Power User achievements | Legendary |

### 🎯 Milestones (15)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🐦 | Early Bird | Use before 6 AM | Uncommon |
| 💯 | Century Mark | 100 messages | Epic |
| 💬 | Talkative | 500 messages | Epic |
| 💬💬 | Legendary Chatter | 1,000 messages | Legendary |
| 🔧 | Tool Fan | 100 total tool calls | Uncommon |
| 🔧🔧 | Tool Addict | 500 total tool calls | Rare |
| 🔧🔧🔧 | Tool Obsessed | 1,000 total tool calls | Epic |
| 💪 | Power Session | 50 tool calls in one session | Rare |
| 📅 | Week Warrior | Use Hermes 7 days in a row | Uncommon |
| 📅📅 | Monthly Master | Use Hermes 30 days in a row | Epic |
| 🌟 | Rare Collector | Unlock all Rare achievements | Epic |
| 🌟🌟 | Epic Collector | Unlock all Epic achievements | Legendary |
| 🎯 | Getting Started Complete | All Getting Started achievements | Epic |
| 🎯 | Tools Complete | All Tools & Skills achievements | Epic |
| 🏆 | Completionist | Unlock every other achievement | Legendary |

### 🤝 Community (11)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| ⭐ | Star Gazer | Reference the Hermes GitHub | Common |
| 🔍 | Plugin Browser | Browse available plugins | Common |
| 🧠 | Skill Browser | Browse available skills | Common |
| 🎨 | Theme Setter | Customize Hermes appearance | Common |
| 📚 | Documentarian | Read the Hermes documentation | Uncommon |
| 🔄 | Updater | Update Hermes to a new version | Uncommon |
| 📋 | Changelog Checker | Read the Hermes changelog | Common |
| 💡 | Feedback Friend | Submit feedback or a suggestion | Uncommon |
| 📝 | Release Reader | Read about a Hermes release | Common |
| 🔍 | Issue Tracker | Reference a GitHub issue or bug | Common |
| 🤝 | Community Member | Reference the Hermes community | Common |

## Architecture

Achievements are detected inline via the `post_llm_call` hook — no separate scanner or cron job needed:

1. **`post_llm_call`** fires after every LLM response, carrying the conversation history with tool call data
2. Achievement checks are O(1) — simple threshold comparisons against accumulated stats
3. **`on_session_end`** tracks session-level metadata (streak days, session count)
4. When an achievement unlocks, a Discord message is posted immediately via raw HTTP API

### File layout

```
~/.hermes/plugins/achievements/
├── __init__.py        # Plugin code: hooks, definitions, detection, commands
├── plugin.yaml        # Plugin metadata (name, version, hooks)
├── pyproject.toml     # Python package metadata
├── CHANGELOG.md       # Version history
├── LICENSE            # MIT License
└── README.md          # This file
```

State data is stored at `~/.hermes/achievements/state.json` (user-local, not part of the repo).

## Development

```bash
# Edit the plugin
vim ~/.hermes/plugins/achievements/__init__.py

# Restart gateway to pick up changes
hermes gateway restart

# View achievements
/achievements
```

### Adding a new achievement

1. Add an entry to `ACHIEVEMENT_DEFS` with a unique `id`, `name`, `emoji`, `description`, `rarity`, and `group`
2. Add detection logic in `_post_llm_call()` — pattern match, threshold check, or stat aggregation
3. Regenerate `NON_COMPLETIONIST_IDS` and group/rarity helper lists (they're derived at load)
4. Update this README with the new achievement in the appropriate group table

## License

MIT
