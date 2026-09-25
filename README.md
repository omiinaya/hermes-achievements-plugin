# Hermes Achievements Plugin 🏆

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Hermes](https://img.shields.io/badge/hermes-agent-plugin-8B5CF6.svg)](https://hermes-agent.nousresearch.com)
[![CI](https://github.com/omiinaya/hermes-achievements-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/omiinaya/hermes-achievements-plugin/actions/workflows/test.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/omiinaya/hermes-achievements-plugin/actions/workflows/test.yml)

**166 Steam-style achievement badges** for [Hermes Agent](https://hermes-agent.nousresearch.com). Unlock achievements as you use Hermes — run commands, search the web, schedule cron jobs, create skills, and explore the platform. Achievements are tracked silently and delivered to your Discord home channel the moment they unlock.

## Quick Start

### Install

**Option A — copy the plugin files (no pip):**
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

**Option B — pip install (wheel, self-contained):**
```bash
pip install hermes-achievements-plugin
```
The wheel ships the manifest and all 4 locale files inside the package data
directory, so a pip-installed copy is fully translated (EN/ES/FR/PT) without
needing a `~/.hermes/plugins/achievements` checkout.

### Requirements

- Hermes Agent 1.0+
- Python 3.11+
- **Discord** (optional): a bot token in `~/.hermes/.env` for Discord embeds:
  ```
  DISCORD_BOT_TOKEN=your_token_here
  DISCORD_HOME_CHANNEL=your_home_channel_id
  ```
- **Non-Discord** (Matrix / Telegram / SimpleX / WhatsApp): no extra config —
  unlock notifications are delivered through `hermes send` to whatever
  home channels are configured for those platforms.
- Optional: `ACHIEVEMENTS_NOTIFY_PLATFORMS=matrix telegram …` in `~/.hermes/.env`
  to limit cross-platform notifications to a specific set of platforms
  (default: every configured home channel).

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

For Discord setups this is a rarity-colored embed (gray → gold) posted asynchronously via a debounced timer — rapid unlock bursts are batched into a single message (capped at 10 embeds per Discord message), never blocking the agent loop.

On non-Discord platforms (Matrix, Telegram, SimpleX, WhatsApp) the unlock is delivered as a plain-text message through `hermes send` to each configured home channel, on a daemon thread so delivery never blocks the hook pipeline. Set `ACHIEVEMENTS_NOTIFY_PLATFORMS` to restrict which platforms are notified.

### Example output

```
/achievements

**🎮 Hermes Achievements**
*Achievements unlock automatically as you use Hermes*

**🔥 Recently Unlocked:**
  ✅ **First Steps** — Send your first message to Hermes
  ✅ **Web Walker** — Search the web using Hermes

🚀 **Getting Started** (2/16) █░░░░░░░░░
🛠️ **Tools & Skills** (2/30) ░░░░░░░░░░
⚡ **Power User** (0/47) ░░░░░░░░░░
👑 **Expert** (0/46) ░░░░░░░░░░
🎯 **Milestones** (0/21) ░░░░░░░░░░
🤝 **Community** (0/6) ░░░░░░░░░░

🔮 Closest to unlock: **Deep Diver** ██░░░░░░░░ 5/25 (20%)

/achievements next

🎯 **Next Up** — closest to unlocking:

🟩 **Deep Diver** — ██░░░░░░░░ 5/25 (20%)
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

### Privacy 🔒

The plugin tracks how you use Hermes and persists progress locally to
`~/.hermes/achievements/state.json` (owner-only permissions, gitignored).
What it stores:

- **Counters and metadata only** — tool names, model/platform/provider
  names, slash-command names, byte sizes of large outputs, word counts,
  token totals, session/turn counts, streaks, timestamps.
- **Platform user identifiers** — for the Social Butterfly / Party Host
  achievements, the plugin records the stable IDs of users who message
  your gateway (e.g. `discord:123456789012345678`). This is the only
  personally-identifiable data retained, and it never leaves your machine.

What it **never** stores or transmits:

- **No message content** — user prompts and assistant responses are only
  measured for length, never saved.
- **No terminal output or tool results** — content is discarded; only byte
  sizes are kept.
- **No tool arguments or file contents** — argument-based achievements only
  check for the presence of specific fields/patterns.
- **No credentials** — the Discord bot token is read from `~/.hermes/.env`
  into memory for the notification `Authorization` header only, and is
  never logged or written anywhere.

The only network egress is a POST to the Discord API when an achievement
unlocks, carrying the achievement name/description/rarity — no usage data,
no message content, no identifiers. There is no telemetry, analytics, or
third-party data sharing.

## Achievement Groups

### 🚀 Getting Started (16)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 👣 | First Steps | Send your first message to Hermes | Common |
| 🔧 | Config Tinkerer | Change a Hermes configuration setting | Common |
| 🖼️ | Show and Tell | Send an image or media attachment to Hermes | Common |
| 🏥 | Clean Bill of Health | Run `hermes doctor` to check system health | Common |
| 🎭 | Model Hopper | Switch to a different AI model | Common |
| 🗣️ | Chatty | Send 25 messages to Hermes | Common |
| 📋 | Slash Commander | Use 3 different slash commands | Common |
| 🔄 | Persistent | Send messages across 3 different sessions | Common |
| 🌱 | Fresh Start | Start a fresh session with /new or /reset | Common |
| 🔄 | Provider Hopper | Use 2 different AI providers | Common |
| 🌙 | Night Owl | Use Hermes after midnight (local time) | Uncommon |
| 🧊 | Icebreaker | Start your first conversation | Uncommon |
| 🛡️ | Cautious | Deny an approval request | Uncommon |
| 💬 | Conversation Habit | Start 10 conversations | Rare |
| 🔥 | Serial Starter | Start 50 conversations | Epic |
| 🗼 | Conversation Colossus | Start 100 conversations | Legendary |

### 🛠️ Tools & Skills (30)

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
| 🌿 | CI Green Thumb | Run a test suite and keep it green | Uncommon |
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
| 📚 | Docs Architect | Author agent-facing docs (5+ doc files) | Rare |
| 💻 | Code Architect | Execute 100 code blocks | Epic |
| ✍️ | Skill Virtuoso | Create 15 skills | Epic |
| 📖📖📖 | Memory Librarian | Save 100 facts to memory | Epic |
| ⏰ | Cron Overlord | Have 15 active cron jobs | Epic |
| 🛠️🛠️ | Complete Toolset | Use every available Hermes tool type at least once | Epic |
| 👥 | Army Commander | Spawn 25 subagents with delegate_task | Epic |

### ⚡ Power User (47)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🔄 | Session Sage | Resume a past session with --continue or /resume | Uncommon |
| 🎣 | Bait and Switch | Run a request where the provider resolved a different model than you asked for | Uncommon |
| 🏄 | Session Surfer | Resume 10 different sessions | Uncommon |
| 🤹 | Double Time | Emit 2 tool calls in a single response | Uncommon |
| 🌊 | Deep Context | Make one API request with 50+ messages in context | Uncommon |
| ✍️ | Wordsmith | Send a single message of 300+ words | Uncommon |
| 🏠 | Local First | Run Hermes against a local/self-hosted model endpoint | Uncommon |
| 💦 | Verbose Output | Produce 100KB+ of output from a single terminal command | Uncommon |
| 🏝️ | Multi-Environment | Run terminal commands in 2 different execution environments | Uncommon |
| 🎙️ | Essayist | Receive a 1000+ word response from the model | Uncommon |
| ✂️ | Cut Short | Hit the model's output token limit (finish_reason=length) | Uncommon |
| ⏰ | Cron Commander | Schedule your first cron job | Rare |
| 🔌 | MCP Master | Add an MCP server connection | Rare |
| 👥 | Agent Swarm | Spawn a subagent with delegate_task | Rare |
| 👤 | Profile Juggler | Create a named Hermes profile | Rare |
| 👤 | Profile Collector | Create 5 Hermes profiles | Rare |
| ⛓️ | Chain Reaction | Chain 2 cron jobs together with context_from | Rare |
| 🎭 | Multi-Model | Use 5 different AI models | Rare |
| 🔄 | Provider Collector | Use 5 different AI providers | Rare |
| 🏗️ | Workflow Builder | Use 8 different tool types in a single session | Rare |
| ⚡ | Quick Draw | Complete 5 tasks with rapid turnaround | Rare |
| ⚡⚡ | Parallel Master | Run 3 subagents in parallel with a single delegate_task | Rare |
| 🎪 | Batch Artist | Emit 5 tool calls in a single response | Rare |
| 🎚️ | Command Center | Use 10 different slash commands | Rare |
| 🎻 | Conductor | Run 3 subagents simultaneously (peak concurrency) | Rare |
| 🎼 | Orchestrator | Use an orchestrator-role subagent | Rare |
| 🪂 | Trust Fall | Approve a command permanently with 'always' | Rare |
| 🎬 | Visual Storyteller | Send 25 images or media attachments | Rare |
| 🖥️ | Self-Hosted | Make 25 API requests to local/self-hosted endpoints | Rare |
| 🌋 | Data Flood | Produce 1MB+ of output from a single terminal command | Rare |
| 🚫 | Ghost Command | Hit exit code 127 (command not found) on a terminal command | Rare |
| 📜 | Novel Author | Receive a 5000+ word response from the model | Rare |
| 🐢 | Slow Thinker | Run a subagent that takes 10+ minutes | Rare |
| 🎚️ | Remixed Output | Receive a response that another plugin transformed before delivery | Rare |
| 🏗️ | Self-Hosted Architect | Operate a self-hosted service stack | Rare |
| 🔐 | YOLO Mode | Run with --yolo flag or disable approval prompts | Epic |
| 🔐 | YOLO Champion | Complete 25 tasks without approval prompts | Epic |
| 🌉 | Gateway Networker | Connect to 3 different messaging platforms | Epic |
| 🧩 | Plugin Pack | Have 5 plugins enabled | Epic |
| 🎭 | Model Collector | Use 10 different AI models | Epic |
| 🎯 | Tool Diversity | Use every available Hermes tool category | Epic |
| 💥 | Parallel Barrage | Emit 10 tool calls in a single response | Epic |
| 🎖️ | Command General | Use 25 different slash commands | Epic |
| 🌌 | Omnipresent | Run terminal commands in 5 different execution environments | Epic |
| 🤖 | Marathon Session | Reach 200 tool calls in a single session | Legendary |
| 🧩 | Plugin Developer | Create your own Hermes plugin | Legendary |
| 🧰 | Tool Torrent | Emit 20 tool calls in a single response | Legendary |

### 👑 Expert (46)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🤿 | Deep Dive | Let Hermes work 10 steps in a single turn | Uncommon |
| 🌍 | Multi-Lingual | Communicate with Hermes in a language other than English | Uncommon |
| 📚 | Doc Diver | Read the Hermes documentation | Uncommon |
| ✋ | Manual Override | Interrupt a running tool call — take manual control | Uncommon |
| 🚧 | Dead End | Hit a tool call blocked by policy before it ran | Uncommon |
| 🛰️ | Remote Warden | Approve a dangerous command from a chat platform | Uncommon |
| 🧨 | Risk Explorer | Approve commands in 5 different danger classes | Uncommon |
| 👻 | Ghosted | Leave an approval prompt unanswered | Uncommon |
| 👁️ | Watchlisted | Get prompted to vet commands in 5 different danger classes | Uncommon |
| 📖 | Novelist | Send a single message of 1500+ words | Rare |
| 📦 | Big Haul | Receive a 1MB+ result from a single tool call | Rare |
| 🛑 | Token Wall | Hit the model's output token limit 25 times (finish_reason=length) | Rare |
| 🌉 | Gateway Guru | Connect Hermes to a messaging platform gateway | Rare |
| 🧩 | Plugin Power | Install and enable a Hermes plugin | Rare |
| 🔧 | Config Guru | Modify 15 different configuration settings | Rare |
| 🎯 | Precision Scheduler | Schedule a one-shot cron job for a specific time | Rare |
| ⚙️ | Environment Tuner | Configure custom environment variables for a cron job | Rare |
| 🪨 | Tenacious | Survive an API request that failed 2+ times in a row | Rare |
| 🔍 | Under Scrutiny | Trigger 10 approval requests | Rare |
| 🔬 | Trial and Error | Persist through 25 tool calls that errored | Rare |
| 🗣️ | Backseat Driver | Interrupt 5 tool calls while they run | Rare |
| 🧱 | Brick Wall | Hit 10 tool calls blocked by policy | Rare |
| 🚁 | Long-Distance Operator | Approve 10 dangerous commands from a chat platform | Rare |
| ⚗️ | Danger Collector | Approve commands in 15 different danger classes | Rare |
| 🤐 | Silent Treatment | Leave 5 approval prompts unanswered | Rare |
| 🕵️ | Person of Interest | Get prompted to vet commands in 15 different danger classes | Rare |
| 🏛️ | Context Colossus | Make one API request with 100+ messages in context | Epic |
| 🧠 | Context Monster | Send one API request with 200K+ input tokens | Epic |
| 🗄️ | Colossal Result | Receive a 10MB+ result from a single tool call | Epic |
| 🏃 | Marathon | Run a subagent that takes 60+ minutes | Epic |
| 🤖 | The 90-Turn Club | Reach 90 tool calls in a single session (default max_turns) | Epic |
| 📡 | Cross-Platform Operative | Chat with Hermes from 2+ different platforms | Epic |
| 🔌 | MCP Wizard | Write a custom MCP server configuration | Epic |
| 🔷 | Rare Collector | Unlock every Rare achievement | Epic |
| 🧗 | Resilient | Complete a task after a subagent failed | Epic |
| 🛡️ | Indestructible | Survive 10 LLM API errors without quitting | Epic |
| ⛰️ | Undeterred | Survive an API request that failed 4+ times in a row | Epic |
| 🎛️ | Control Freak | Interrupt 15 tool calls — you like to be in charge | Epic |
| ☢️ | Living on the Edge | Approve commands in 25 different danger classes | Epic |
| 🚨 | Most Wanted | Get prompted to vet commands in 25 different danger classes | Epic |
| 🌊 | Token Tsunami | Send one API request with 500K+ input tokens | Legendary |
| 📡📡 | Cross-Platform Veteran | Chat with Hermes from 5+ different platforms | Legendary |
| 🪝 | Hook Master | Create a plugin using 3+ different hook types | Legendary |
| 📈 | CLI Champion | Execute 500 terminal commands | Legendary |
| 💪 | Ultra Marathon | Reach 150 tool calls in a single session | Legendary |
| 🧬 | Mutant Slayer | Run mutation testing to harden a test suite | Legendary |

### 🎯 Milestones (21)

| Icon | Name | Description | Rarity |
|------|------|-------------|--------|
| 🐦 | Early Bird | Use Hermes before 6 AM | Uncommon |
| 🔧 | Tool Fan | Accumulate 100 total tool calls | Uncommon |
| 💧 | Token Tyro | Consume 100,000 tokens across all sessions | Uncommon |
| 🔧 | Tool Addict | Accumulate 500 total tool calls | Rare |
| 📅 | Week Warrior | Use Hermes 7 days in a row | Rare |
| 💪 | Power Session | Make 50 tool calls in a single session | Rare |
| ⚡ | Speed Demon | Get 25 API responses in under 2 seconds | Rare |
| 💯 | Century Mark | Accumulate 100+ messages across all sessions | Epic |
| 💬 | Talkative | Send 500 messages total | Epic |
| 🔧 | Tool Obsessed | Accumulate 1,000 total tool calls | Epic |
| 🧙 | Token Wizard | Consume 1,000,000 tokens across all sessions | Epic |
| 🚀 | Getting Started Complete | Unlock every Getting Started achievement | Epic |
| 📦 | Release Discipline | Cut a tagged versioned release | Epic |
| 🧱 | Commit Craftsman | Land 25 git commits across working trees | Epic |
| 🏆 | Completionist | Unlock every other achievement | Legendary |
| 💬💬 | Legendary Chatter | Send 1,000 messages total | Legendary |
| 📅📅 | Monthly Master | Use Hermes 30 days in a row | Legendary |
| 🐋 | Token Whale | Consume 10,000,000 tokens across all sessions | Legendary |
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

Achievements are detected via eighteen plugin hooks — no separate scanner or cron job needed:

1. **`pre_llm_call`** fires once per turn *before* the LLM is invoked, carrying `is_first_turn` — True only when `run_conversation` was handed no prior history. That makes it the one signal that counts natural conversation starts: session creation (`on_session_start`) can fire without a message, and `/new` or `/reset` (`on_session_reset`) are explicit rotations. Fresh contexts drive Icebreaker (1), Conversation Habit (10), Serial Starter (50), and Conversation Colossus (100).
2. **`pre_tool_call`** fires once per tool call *before* execution, carrying `api_request_id` — the ID of the assistant response that emitted the call. All tool calls from one response share that ID, so counting consecutive calls per ID measures how many tools the model batched into a single step: Double Time (2), Batch Artist (5), Parallel Barrage (10), and Tool Torrent (20). This is distinct from delegate_task parallelism (Parallel Master) and from cumulative tool counts.
3. **`transform_terminal_output`** fires per terminal command with the FULL raw output *before* the terminal tool truncates it (default ~50KiB head+tail) — the only hook that sees what the model was NOT handed. Raw output volume drives Verbose Output (100KB) and Data Flood (1MB); `env_type` (local/ssh/docker/singularity/modal/daytona) diversity drives Multi-Environment (2) and Omnipresent (5); the numeric `returncode` detects exit code 127 — command not found — for Ghost Command (post_tool_call only buckets ok/error, it cannot express specific codes). The handler is a strict observer: it always returns None so command output is never altered.
4. **`transform_tool_result`** fires per tool call with the FULL result string — post_tool_call only gets status/error_type, never the content. Result size measures context bloat: how much data a single tool pushed into the conversation drives Big Haul (1MB) and Colossal Result (10MB). Also a strict observer — always returns None.
5. **`post_tool_call`** fires after *every* tool execution with the full tool arguments. This is the primary detection path: per-tool usage counters, per-session tool tracking, argument-based achievements (cron job chaining via `context_from`, parallel delegation via `tasks`, plugin/hook authoring via file content, skill creation), and tool-error resilience (the gateway's `status="error"` feeds Trial and Error — 25 failed calls).
6. **`post_llm_call`** fires once per turn and handles per-turn signals: cumulative message counts, model/platform diversity, user-command pattern matching (`hermes doctor`, `/title`, `--yolo`, ...), tiered command counters (config changes, plugins enabled, skills installed), group/rarity completion checks, user-message verbosity (Wordsmith 300, Novelist 1500) — and model-response verbosity (Essayist at a 1000-word reply, Novel Author at 5000): a mirror dimension measuring what the *model* wrote, strictly separated from the user's input length.
7. **`post_api_request`** fires once per successful provider API request with normalized `usage` token buckets, `api_duration` in seconds, and `finish_reason`. It powers the token-consumption milestones (Token Tyro/Wizard/Whale at 100K/1M/10M tokens), the fast-response achievement (Speed Demon — 25 responses under 2s), and the "Tokens consumed" stat — plus output-cap truncation: `finish_reason="length"` means the model hit its max output tokens and was cut off mid-response, driving Cut Short (first hit) and Token Wall (25 hits). Usage buckets show how many tokens were consumed; only `finish_reason` reveals the response was *incomplete*.
8. **`pre_api_request`** fires once per provider API request *before* it's sent, carrying `base_url` and `approx_input_tokens`. It detects local/self-hosted model endpoints (Local First on first local call, Self-Hosted at 25) and single-request input-token spikes (Context Monster at 200K, Token Tsunami at 500K) — the endpoint topology and one-shot context size, distinct from the post hook's cumulative totals.
9. **`on_session_start`** counts distinct sessions (drives the Persistent / session milestones).
10. **`on_session_end`** tracks daily streaks (Week Warrior, Monthly Master) and re-checks completions.
11. **`subagent_stop`** fires once per child agent after `delegate_task` finishes, with `child_role`, `child_status`, and `duration_ms`. This is the authoritative subagent count (a single call with 3 tasks spawns 3 children), driving Army Commander (25 children), Orchestrator (orchestrator role), and Resilient (failed/interrupted child) — and subagent runtime: a 10-minute child (Slow Thinker) is a very different delegation than a 10-second one (Marathon at 60 minutes).
12. **`subagent_start`** fires when a subagent is spawned. It increments a live concurrency counter that `subagent_stop` decrements — the peak (max simultaneous children) drives Conductor (3 concurrent subagents). This is true parallelism, not just call counting.
13. **`post_approval_response`** fires after the user responds to an approval prompt, with `choice` (once/session/always/deny/timeout), `surface` (cli/gateway), and `pattern_keys` (the dangerous-command classes that matched). Choosing *always* (permanent trust) unlocks Trust Fall and counts toward YOLO Mode / YOLO Champion; choosing *deny* unlocks Cautious. Beyond the choice itself, two approval-context dimensions: `surface="gateway"` means the user approved a dangerous command from a chat platform — bolder than at the CLI — driving Remote Warden (1) and Long-Distance Operator (10); and the set of DISTINCT danger classes approved (rm, chmod, mkfs, DROP TABLE, curl|sh, git push --force...) drives Risk Explorer (5), Danger Collector (15), and Living on the Edge (25) — breadth of risk appetite, deduped so approving `rm` ten times counts as one class.
14. **`pre_approval_request`** fires when an approval prompt is raised, before the user answers. It counts how often commands trigger approval gates — 10 gates unlock Under Scrutiny, independent of how the user responds (attempted gates, not consent; the class/surface dimensions live on `post_approval_response` where the choice is known).
15. **`on_session_reset`** fires when the gateway swaps in a fresh session key (`/new`, `/reset`) — drives Fresh Start and the session-resets counter.
16. **`api_request_error`** fires when an LLM provider call fails (invalid response, rate limit, timeout, retries exhausted). Surviving 10 such errors without quitting unlocks Indestructible — and the `retry_count` of the failing request measures sustained-outage depth: Tenacious (2 consecutive failures of the same request) and Undeterred (4).
17. **`pre_gateway_dispatch`** fires once per incoming user-originated message, before auth. It is the ONLY hook that sees messages from *other* users (everything else fires for agent turns) — distinct senders drive Social Butterfly (3 users) and Party Host (10 users), and media attachments (Show and Tell, Visual Storyteller). It also sees slash commands the gateway intercepts BEFORE the LLM (`/new`, `/reset`, `/title`, `/achievements` — 56 known commands never reach the model, so post_llm_call can't count them): Command Center (10 distinct commands) and Command General (25). Only the platform's primary user (first non-bot seen — the owner) counts, so strangers' commands in shared channels don't unlock the user's achievements.
18. **`on_session_finalize`** fires when the gateway shuts down an agent or a session's reset policy expires. It force-flushes the debounced state save and synchronously delivers any notifications still in the debounce window — nothing is lost when the process exits.

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
# simulation that proves all 166 achievements can unlock
python3 -m pytest tests/ -q

# Run the one-shot health check (defs, locales, manifest↔register hooks,
# live state) — add --manifest to also load through the real PluginManager
python3 scripts/check_plugin.py --live --manifest

# Release: bump version, update CHANGELOG.md, commit, push, tag.
# Pushing a v* tag triggers .github/workflows/release.yml, which re-runs
# tests, builds the wheel, and publishes the GitHub Release automatically.
python3 scripts/bump_version.py 2.7.0
# ... edit CHANGELOG.md with the new entry ...
git add -A && git commit -m "release: v2.7.0 — ..."
git tag v2.7.0 && git push origin main && git push origin v2.7.0

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
