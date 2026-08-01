# Changelog

## [2.16.0] — 2026-08-01

### Added

- **Sustained-failure resilience** — `api_request_error` now reads
  `retry_count` (consecutive failures the SAME request survived before the
  hook fired; the gateway retry loop fires the hook once per failed
  attempt, incrementing depth): Tenacious 🪨 (rare, Expert) at depth 2
  (reachable on default `api_max_retries=3`), Undeterred ⛰️ (epic, Expert)
  at depth 4 (requires raising `api_max_retries`). Distinct from
  Indestructible's total-error breadth — 10 requests failing once each
  never reach depth 2 (tested). `max_retry_depth` stat + stats-view line.

### Fixed

- **Def-id collision (found in this release)** — a new Expert achievement
  accidentally reused the id `persistent`, which already existed as a
  Getting Started achievement ("Send messages across 3 different
  sessions"). Python dict-literal duplicate keys collapse silently (last
  wins), so the old def was shadowed, its handler redirected to the new
  def, and three locale translations were clobbered. Renamed the new
  achievement to `tenacious` and restored the old translations. Two
  regression guards added: `test_no_duplicate_id_keys_in_source` counts
  raw `"id":` keys in the defs literal (the parsed `test_unique_ids`
  cannot see literal collisions), and `update_locales.py` now only lets
  NEW translations overwrite stale English fallbacks — a real translation
  is never clobbered (WARN instead).
- **Gateway scan gap** — `run_agent.py` added to check_plugin.py's
  gateway source candidates: `api_request_error` dispatches there
  (`invoke_hook("api_request_error", ...)` with `retry_count`), so the
  kwarg contract check was silently missing it. 32 keys now checked
  across 13 hooks.

## [2.15.0] — 2026-08-01

### Changed

- **Coverage hardened to 100%** — `__init__.py` now has full line
  coverage (was 14 missed lines at 99%). New `TestCoverageEdges` suite
  (9 tests) closes every defensive/normalization branch the feature
  suites never reach: the `_load_state` lock double-check (deterministic
  thread test), list→set `env_types` migration for older persisted
  state, `_is_local_base_url` non-string/empty/malformed-URL fallbacks
  (`urlparse` ValueError on unclosed IPv6 brackets), media detection via
  `message_type` alone (e.g. "voice" with no media_urls), the non-compact
  badge-with-progress rendering path, `_format_bytes` sub-KiB branch, and
  `_format_duration` hours branch.
- **Dead code eliminated** — `_format_bytes`' trailing fallback was
  provably unreachable (the `units[-1]` guard guarantees a return on the
  last loop iteration); replaced with a defensive `AssertionError`.
- **CI coverage gate raised 95% → 99%** — the full tree sits at 99.7%
  (the only misses are inside the test files themselves, which would be
  circular to test); a 99% floor means a new hook branch can no longer
  ship untested. 341 tests total.

## [2.14.0] — 2026-08-01

### Added

- **Tool-status dimension** — `post_tool_call` now reads the full `status`
  surface the gateway delivers, not just `error`. Two genuinely new
  observation dimensions, verified against the installed Hermes source:
  - **User interrupts** — `status="cancelled"` fires when the user presses
    stop while a tool is running (`error_type` keyboard_interrupt): Manual
    Override ✋ (uncommon, Expert) on the first interrupt, Backseat Driver
    🗣️ (rare) at 5, Control Freak 🎛️ (epic) at 15. Distinct from tool
    errors (execution failed) and from approvals (consent prompts the
    user answers) — an interrupt is the user actively taking control.
  - **Policy blocks** — `status="blocked"` fires when scope/plugin/
    guardrail policy denies a tool BEFORE it runs: Dead End 🚧 (uncommon,
    Expert) on the first block, Brick Wall 🧱 (rare) at 10. A block is
    environmental policy, invisible to every other hook.
  - `tool_interrupts` / `tool_blocks` counters + stats-view lines.
  - Also fixed the stale status enum in the handler comment (the gateway
    emits `"blocked"`, not `"block"`).
- New `TestToolInterrupts` (4 tests) + `TestToolBlocks` (3 tests) suites
  with negative cases (ok/error statuses never count), tier cascade, and
  counter assertions; grind fires 15 interrupts + 10 blocks; stats-view
  tests. **139 → 144 achievements** (Expert 28 → 33).

## [2.13.1] — 2026-08-01

### Fixed

- **Lint gate** — `ruff check .` (the CI `test.yml` Lint step) had been
  failing since v2.9.0 without breaking the release workflow (which only
  runs pytest): SIM103 in `_is_local_base_url` (early-return chain →
  single boolean expression), RET501/PLR1711 on the two transform-hook
  observer `return None` statements (kept with targeted noqa — the
  observer contract is load-bearing: a string return would REPLACE
  command output / tool results), and an unused `sys` import in
  `scripts/render_readme.py`. Test workflow is green again; the lint
  gate is now documented in AGENTS.md.

## [2.13.0] — 2026-08-01

### Added

- **Model-response verbosity** — `post_llm_call` now reads
  `assistant_response` (the model's OWN output text, delivered but
  previously ignored). Mirrors the user-verbosity dimension for what the
  MODEL wrote: Essayist 🎙️ (uncommon, Power User) on a 1000-word reply;
  Novel Author 📜 (rare) at 5000 words. Strictly separated from
  user-message length (Wordsmith/Novelist) — a long user message never
  unlocks these, and vice versa (tested). `longest_response_words` stat.
- **Output-cap truncation** — `post_api_request` now reads `finish_reason`.
  `finish_reason="length"` means the model hit its max output tokens and
  was cut off mid-response: Cut Short ✂️ (uncommon, Power User) on the
  first hit; Token Wall 🛑 (rare, Expert) at 25. Usage buckets show how
  many tokens were consumed — only `finish_reason` reveals the response
  was *incomplete* (new dimension, 133 → 139).
- **Subagent runtime** — `subagent_stop` now reads `duration_ms` (how long
  a delegated child actually ran, delivered but previously ignored):
  Slow Thinker 🐢 (rare, Power User) on a 10-minute child; Marathon 🏃
  (epic, Expert) at 60 minutes. Child-counting cannot see this — a
  10-minute delegation is a very different event than a 10-second one.
  `longest_subagent_ms` stat with `_format_duration` (e.g. "12m 30s").
- New `TestModelResponseVerbosity` suite (7 tests: thresholds, peak-max,
  missing-response no-op, progress, user/model dimension separation),
  `TestTruncation` suite (5 tests: stop/tool_calls no-op, first hit,
  25-hit wall, mixed counting, missing no-op), and `TestSubagentRuntime`
  suite (5 tests: fast no-op, 10m, 60m, peak-max, missing no-op).
- Grind extended: 3 turns with 5200-word responses, 30 requests with
  `finish_reason="length"`, one 65-minute subagent.
- 3 new `ui.*` stats keys (`stats_longest_response`, `stats_truncations`,
  `stats_longest_subagent`) — all 4 locales, real es/fr/pt translations.

### Changed

- Power User group: 38 → 42 achievements; Expert: 26 → 28. Group headers
  and the group-counts test updated. Compact group views keep every
  group (largest: Power User, 42) under Discord's 2000-char cap.
- Tests: 302 → 322; all green. Health check: 31 kwargs across 12 hooks
  (`finish_reason`, `assistant_response`, `duration_ms` confirmed
  delivered by the installed Hermes source).

## [2.12.0] — 2026-08-01

### Added

- **17th plugin hook: `transform_terminal_output`** — fires per terminal
  command with the FULL raw output *before* the terminal tool truncates it
  (~50KiB head+tail default) — the only hook that sees what the model was
  NOT handed. Also carries `env_type` (local/ssh/docker/singularity/modal/
  daytona) and the numeric `returncode`. New dimensions (126 → 133):
  - **Raw output volume** — Verbose Output 💦 (uncommon, Power User) on
    one command producing 100KB+; Data Flood 🌋 (rare) at 1MB+.
    `peak_terminal_output_bytes` stat surfaced in the stats view.
  - **Execution-environment diversity** — Multi-Environment 🏝️ (uncommon)
    on 2 distinct env types; Omnipresent 🌌 (epic) at 5. `env_types` set
    stat (JSON-safe, sorted list on save).
  - **Numeric exit codes** — Ghost Command 🚫 (rare, secret) on exit code
    127 ("command not found") — a signal `post_tool_call`'s ok/error
    status bucket cannot express.
- **18th plugin hook: `transform_tool_result`** — fires per tool call with
  the FULL result string (post_tool_call only gets status/error_type,
  never the content). New dimension:
  - **Tool-result size / context bloat** — Big Haul 📦 (rare, Expert) on
    a single tool result ≥ 1MB; Colossal Result 🗄️ (epic) at 10MB+.
    `peak_tool_result_bytes` stat surfaced in the stats view.
- **Observer-only transform contract** — both transform hooks always
  return `None`, so the plugin never alters command output or tool
  results (tested explicitly). `transform_llm_output` was deliberately
  NOT registered: its kwargs (`response_text`/`session_id`/`model`/
  `platform`) are a strict subset of what `post_llm_call` already
  observes — registering it would add no new dimension.
- New `TestTransformTerminalOutput` suite (12 tests: observer contract,
  output thresholds, peak-max, exit-127, env diversity, repeat-env
  dedup, progress, JSON-safe persistence) and `TestTransformToolResult`
  suite (7 tests: observer contract, size thresholds, peak-max, progress,
  empty no-op). Grind extended with 2MB output, 5 env types, exit 127,
  and 20MB tool result. Gateway scan now covers `tools/terminal_tool.py`.
- 3 new `ui.*` stats keys (`stats_peak_terminal_output`,
  `stats_peak_tool_result`, `stats_env_types`) with a `_format_bytes`
  human-readable helper — all 4 locales, real es/fr/pt translations.

### Changed

- Power User group: 33 → 38 achievements; Expert: 24 → 26. Group headers
  and the group-counts test updated. Compact group views keep every
  group (largest: Power User, 38) under Discord's 2000-char cap.
- Tests: 280 → 302; all green.

## [2.11.0] — 2026-08-01

### Added

- **16th plugin hook: `pre_tool_call`** — fires once per tool call
  *before* execution, carrying `api_request_id` (the ID of the assistant
  response that emitted the call). Every tool call from one response
  shares that ID, so counting consecutive calls per ID reveals how many
  tools the model batched into a single step — a dimension `post_tool_call`
  cannot see (it has no `api_request_id`). New dimension (122 → 126):
  - **Single-response tool batching** — Double Time 🤹 (uncommon, Power
    User) on 2 tool calls in one response; Batch Artist 🎪 (rare) at 5;
    Parallel Barrage 💥 (epic) at 10; Tool Torrent 🧰 (legendary) at 20.
    `peak_tools_per_response` stat (max, not last) surfaced in the stats
    view (`ui.stats_peak_batch`, all 4 locales).
- **Compact group views** — the group-filter command (`/achievements
  power_user` etc.) now renders compact badges (icon + name + short
  progress) instead of full descriptions, keeping every locale's largest
  group (Power User, 33) under Discord's 2000-char cap. Descriptions stay
  one `/achievement <id>` away. New `ui.badge_compact_format` key in all
  4 locales.
- New `TestPreToolCall` suite (10 tests: batch thresholds, cross-response
  reset, missing-id no-op, peak-max, progress tracking). Grind extended
  with a 25-call response plus a smaller second response proving all four
  new defs unlock and the reset path works. check_plugin.py gateway scan
  now also covers `hermes_cli/plugins.py` (where pre_tool_call dispatches).

## [2.10.0] — 2026-08-01

### Added

- **15th plugin hook: `pre_llm_call`** — fires once per turn *before* the
  LLM is invoked, carrying `is_first_turn` (True only when
  `run_conversation` was handed no prior history). This is the one signal
  that counts natural conversation starts: session creation
  (`on_session_start`) can fire without a message, and `/new` or `/reset`
  (`on_session_reset`) are explicit user rotations rather than context
  boundaries. New dimension (118 → 122):
  - **Fresh-conversation count** — Icebreaker 🧊 (uncommon, Getting
    Started) on the first fresh context; Conversation Habit 💬 (rare) at
    10; Serial Starter 🔥 (epic) at 50; Conversation Colossus 🗼
    (legendary) at 100. `conversations_started` counter persisted +
    stats-view line (`ui.stats_conversations`, all 4 locales).
- New `TestPreLlmCall` suite (7 tests: first-turn unlock, no-op guards for
  `is_first_turn=False` and missing flag, all four tier thresholds,
  progress tracking). Grind extended with 105 fresh-context fires so all
  four new defs are proven unlockable through real hook calls.

## [2.9.0] — 2026-08-01

### Added

- **14th plugin hook: `pre_api_request`** — fires once per provider API
  request *before* it's sent, carrying `base_url` (the endpoint host),
  `approx_input_tokens` (the preflight input-token estimate for THIS
  request), `api_mode`, and `max_tokens`. Two new dimensions:
  - **Endpoint topology (114 → 116)** — where the model runs. Local
    First 🏠 (uncommon, Power User) unlocks on the first request to a
    loopback/private/self-hosted endpoint (`localhost`, `127.0.0.1`,
    `192.168.*`, `10.*`, `172.16–31.*`, `169.254.*`, `*.local`,
    `*.internal`, `*.lan`); Self-Hosted 🖥️ (rare, Power User) at 25 such
    requests. `local_requests` counter persisted + stats-view line
    (`ui.stats_local_requests`, all 4 locales).
  - **Single-request input-token spike (116 → 118)** — how big ONE
    request's context window is, distinct from cumulative token
    milestones. Context Monster 🧠 (epic, Expert) at 200K+
    `approx_input_tokens`; Token Tsunami 🌊 (legendary, Expert) at 500K+.
    `peak_input_tokens` stat (max, not last) surfaced in the stats view
    (`ui.stats_peak_input`, all 4 locales). Crossing 500K also unlocks
    the 200K tier.
- New `TestPreApiRequest` suite (10 tests: localhost/private-IP/suffix
  detection, cloud-doesn't-count, 25-request tier, 200K/500K thresholds,
  max-keeping, missing/invalid kwargs) + 2 new stats-view tests + format
  args. Full-grind simulation now cycles 60 `pre_api_request` calls with
  alternating local/cloud base_urls and `approx_input_tokens` escalating
  to 905K — the all-118-unlockable invariant covers both new dimensions
  end-to-end.
- All 4 new achievements translated across es/fr/pt (Local Primero /
  Local d'Abord / Local Primeiro, Autoalojado / Auto-Hébergé /
  Auto-Hospedado, Monstruo de Contexto / Monstre de Contexte / Monstro
  de Contexto, Tsunami de Tokens ×3).

### Changed

- Hook kwarg contract check now verifies **21 keys across 8 hooks** —
  `pre_api_request`'s `base_url` and `approx_input_tokens` confirmed
  delivered by the installed Hermes source (agent/conversation_loop.py).
- Plugin manifest and README/AGENTS.md hook tables updated to the 14-hook
  architecture.

## [2.8.0] — 2026-08-01

### Added

- **Media dimension (108 → 110)** — `pre_gateway_dispatch`'s `MessageEvent`
  carries `media_urls` (local file paths for the vision tool), `media_types`,
  and `message_type` (PHOTO/VIDEO/AUDIO/DOCUMENT/…), but the plugin only ever
  read the sender identity. Now every user-originated message with any media
  signal counts:
  - **Show and Tell 🖼️** (common, Getting Started) — send an image or media
    attachment to Hermes
  - **Visual Storyteller 🎬** (rare, Power User) — send 25 media messages
  - `media_messages` counter persisted + surfaced in the stats view
    (`ui.stats_media`, all 4 locales).
  - Detection is signal-OR: non-empty `media_urls` OR `media_types` OR a
    non-TEXT/COMMAND `message_type` — so inline images that keep
    `message_type="text"` still count.
- **Context-depth dimension (110 → 112)** — `post_api_request` delivers
  `message_count` (the number of messages sent in that single API request =
  system prompt + full conversation history + tool results). The plugin
  tracked cumulative turns and tokens but never *how much context the model
  chewed through in one shot*:
  - **Deep Context 🌊** (uncommon, Power User) — one API request with 50+
    messages in context
  - **Context Colossus 🏛️** (epic, Expert) — one API request with 100+
    messages in context
  - `peak_context_messages` stat (max, not last) surfaced in the stats view
    (`ui.stats_peak_context`, all 4 locales). Crossing the 100 threshold also
    unlocks the 50 one.
- **Message-verbosity dimension (112 → 114)** — `post_llm_call` delivers the
  raw `user_message`, which the plugin only scanned for non-ASCII letters.
  Word count is a distinct usage pattern — a detailed spec in one message vs
  drip-feeding context:
  - **Wordsmith ✍️** (uncommon, Power User) — send a single message of 300+
    words
  - **Novelist 📖** (rare, Expert) — send a single message of 1500+ words
  - `longest_message_words` stat (max, not last) surfaced in the stats view
    (`ui.stats_longest_message`, all 4 locales). Crossing 1500 also unlocks
    the 300 one.
- New `TestPostApiRequest` suite (context-depth edge cases: threshold,
  max-keeping, missing/zero `message_count`), media tests in
  `TestPreGatewayDispatch` (signal-OR detection, text-doesn't-count,
  progress), verbosity tests in `TestPerTurnSignals` (max-keeping, empty
  message), and 4 new stats-view tests. Full-grind simulation now escalates
  `message_count` to 139, fires 30 media events, and posts a 1600-word
  message — the all-114-unlockable invariant covers all three new dimensions
  end-to-end.
- All 6 new achievements translated across es/fr/pt (`update_locales.py`
  entries: Muestra y Cuenta / Montre et Raconte / Mostre e Conte, …).

### Changed

- Hook kwarg contract check now verifies 20 keys across 7 hooks —
  `message_count` on `post_api_request` is confirmed delivered by the
  installed Hermes source.
- `render_readme.py` no longer warns on a non-108 count (the exact-count
  assertion lives in tests; the script renders the defs as source of truth).

## [2.7.0] — 2026-08-01

### Added

- **Deep Dive 🤿 (108th achievement)** — `post_api_request` delivers `api_call_count`, which resets to 0 at the start of every user turn and increments per provider call. A count ≥ 10 means the agent ran a long autonomous multi-step stretch (tool loop, delegations, retries) without user intervention — a genuinely new *turn-depth* dimension, distinct from cumulative message/tool counts. One turn with 10+ provider calls unlocks it (Expert group).
- Full-grind simulation escalates `api_call_count` to 14, so the all-108-unlockable invariant covers the new dimension end-to-end.
- `deep_dive` translated across all 4 locales (Buceo Profundo / Plongée Profonde / Mergulho Profundo).

### Changed

- **Release automation** — pushing a `v*` tag now triggers `.github/workflows/release.yml`: re-runs tests, builds the wheel, verifies the wheel payload (code, plugin.yaml, all 4 locales, LICENSE), and publishes the GitHub Release automatically with the wheel attached. Tag push is the approval signal; direct publish (no draft) so the unpublished-release gap can't recur. Release flow documented in README Development section.
- **First GitHub Release** — the repo had shipped 8 versions with zero releases; v2.5.0 and v2.6.0 releases now exist with wheels attached.

## [2.6.0] — 2026-08-01

### Added

- **Provider diversity dimension (105 → 107)** — the plugin tracked models and platforms but had no visibility into *which LLM providers* were in use, even though `post_api_request` delivers `provider` on every successful API call. Two new achievements:
  - **Provider Hopper 🔄** (common, Getting Started) — use 2 different AI providers
  - **Provider Collector 🔄** (rare, Power User) — use 5 different AI providers
- **Stats view now shows `Providers: …`** (`ui.stats_providers`, all 4 locales) — mirrors the Models line, with the same "+N more" suffix beyond 3. The first infrastructure-level stat.
- Full-grind simulation now cycles 8 synthetic providers through `_post_api_request`, so the all-107-unlockable invariant covers the new dimension end-to-end.
- **First GitHub Release created** — the repo had shipped 8 versions (v2.4.3 → v2.5.0) with zero GitHub Releases: no release notes, no downloadable wheels. v2.5.0's wheel is now attached to its release; the process gap is documented and the release flow is now part of the dev loop.
- `stats_providers` format test + 3 new stats-view tests (shown, more-suffix, both locales-safe).

### Changed

- `providers_used` added to state persistence + normalization (JSON-safe set round-trip), so provider progress survives gateway restarts.

## [2.5.0] — 2026-07-31

### Added

- **13th plugin hook: `post_api_request`** — fires once per successful provider API request and carries normalized `usage` token buckets plus `api_duration` in seconds. Opens a genuinely new observation dimension — token consumption and latency — previously invisible to the plugin (message counts were tracked, tokens were not).
- **4 new achievements (100 → 104, all in Milestones):** Token Tyro 💧 (100K total tokens), Token Wizard 🧙 (1M), Token Whale 🐋 (10M), Speed Demon ⚡ (25 API responses under 2s). The `usage` shape is provider-normalized by the gateway; the handler also falls back to `prompt_tokens + completion_tokens` when `total_tokens` is absent.
- **Stats view now shows `Tokens consumed: N`** (`ui.stats_tokens`, all 4 locales) — the first cost-oriented stat.
- Full-grind simulation extended to drive `_post_api_request` (1050 requests, mixed usage shapes, alternating fast/slow durations) — the all-105-unlockable invariant now covers the new dimension end-to-end.
- **Health check `--gateway` mode + hook kwarg contract check** — cross-verifies every `kwargs.get("...")` key the plugin reads against the kwargs the installed Hermes source actually passes to each hook. Catches the silent-no-op failure class: if a Hermes update renames a hook kwarg, achievements would quietly stop firing with green tests. Verified 17 keys across 7 hooks all delivered; the check is wired into `setup.sh --test` and covered by a CI test (skips gracefully where Hermes source is absent).
- **Trial and Error 🔬 (105th achievement)** — the gateway passes `status` (ok/cancelled/block/error) and `error_type` to `post_tool_call`; the plugin previously ignored them. Now `status="error"` feeds a `tool_errors` counter — 25 failed tool calls unlock Trial and Error (Expert group). The full-grind simulation drives 30 error-status calls so the all-105-unlockable invariant covers the new dimension.
- **Notification User-Agent regression guard** — verified delivery end-to-end against the live Discord API (200 OK with the plugin's exact headers; the July 13–15 "403 Forbidden" notification failures were a stale token, now valid). New test asserts every notification request carries both `Authorization: Bot …` and a `User-Agent` — Cloudflare (Discord's CDN) rejects header-less API calls with HTTP 403 error code 1010, which would silently kill all notifications.

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
