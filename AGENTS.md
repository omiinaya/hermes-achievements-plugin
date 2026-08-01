# AGENTS.md — Hermes Achievements Plugin

## What this is

A Hermes Agent plugin that awards 153 Steam-style achievement badges for
using Hermes. Pure Python stdlib, no external dependencies.

## Repo layout

- `__init__.py` — everything: achievement definitions, detection hooks,
  slash command handlers, state persistence, Discord notifications
- `plugin.yaml` — plugin manifest (name, version, declared hooks)
- `locales/` — en/es/fr/pt JSON translations
- `tests/test_plugin.py` — static validation (defs, locales, file integrity)
- `tests/test_detection.py` — functional tests driving the hooks with
  synthetic gateway kwargs
- `scripts/render_readme.py` — regenerates every DERIVED section of
  README.md from `ACHIEVEMENT_DEFS` + the recognition maps: the group
  tables, the header badge count, and the `### Example output` block
  (group denominators/bars, next-up thresholds/bars/percents, closest
  hint — the numerators are the only hand-picked numbers, pinned in
  `EXAMPLE_GROUP_PROGRESS`/`EXAMPLE_NEXT_UP`). Fails loudly if a line is
  missing; the suite diff-gates the result locally.
- `scripts/update_locales.py` — auto-syncs achievement keys across all 4
  locales from `ACHIEVEMENT_DEFS` (source of truth): prunes dead keys,
  backfills missing translations (WARN + English fallback)
- `scripts/bump_version.py` — updates the version in all 4 places that
  carry it (pyproject.toml, plugin.yaml, setup.sh ×2) in one shot
- `scripts/check_plugin.py` — health check: module loads, manifest↔register()
  hook agreement, exactly-153 defs, locale parity, no dead detection-map
  references, live state.json reconciliation (--live), real PluginManager
  load (--manifest), and hook kwarg contract vs the installed Hermes
  source (--gateway — catches silent no-op drift if Hermes renames a
  hook kwarg). Run after any swap:
  `python3 scripts/check_plugin.py --live --manifest --gateway`

## Detection architecture (18 hooks)

| Hook | Fires | Owns |
|------|-------|------|
| `pre_tool_call` | once per tool call BEFORE execution (has `api_request_id` — the ID of the assistant response that emitted the call; every call from one response shares it) | single-response tool-batch counting (Double Time 2, Batch Artist 5, Parallel Barrage 10, Tool Torrent 20) — how many tools the model ran in one step, distinct from cumulative counts and delegate_task parallelism |
| `transform_terminal_output` | per terminal command with the FULL raw output BEFORE the tool truncates it (has `output`, `returncode`, `env_type` local/ssh/docker/singularity/modal/daytona) | raw output volume (Verbose Output 100KB, Data Flood 1MB) — the only hook that sees what the model was NOT handed; env diversity (Multi-Environment 2, Omnipresent 5); exit code 127 (Ghost Command). TRANSFORM hook — observer returns None, never a string |
| `pre_llm_call` | once per turn BEFORE the LLM is invoked (has `is_first_turn` — True only when run_conversation was handed no prior history) | fresh-conversation counting (Icebreaker 1, Conversation Habit 10, Serial Starter 50, Conversation Colossus 100) — the only signal that counts natural context starts |
| `transform_tool_result` | per tool call with the FULL result string (has `result`, `api_request_id`, `error_message`) | result-size / context bloat (Big Haul 1MB, Colossal Result 10MB) — post_tool_call only gets status, never the content. TRANSFORM hook — observer returns None, never a string |
| `post_tool_call` | every tool execution (has `tool_name`, `args`, `session_id`, `duration_ms`, `status` ok/cancelled/blocked/error, `error_type`) | per-tool counts, per-session tracking, argument-based achievements (cron chaining, parallel delegation, skill/plugin authoring), Quick Draw, tool-status counting (Trial and Error — 25 failed calls; Manual Override 1 / Backseat Driver 5 / Control Freak 15 — user interrupts, `status="cancelled"` with `error_type` keyboard_interrupt, the user pressing stop mid-tool; Dead End 1 / Brick Wall 10 — policy blocks, `status="blocked"` when scope/plugin/guardrail policy denies the tool BEFORE it runs). Distinct from approvals (consent prompts the user answers) — an interrupt is active user control, a block is environmental policy |
| `post_llm_call` | once per turn (has `assistant_response` — the model's own output text) | cumulative message counts, model/platform diversity, user-command patterns, tiered counters, group/rarity completions, message verbosity (Wordsmith 300 words, Novelist 1500), model-response verbosity (Essayist 1000 words, Novel Author 5000 — a mirror dimension measuring what the MODEL wrote, distinct from user input) |
| `post_api_request` | once per successful provider API request (has `usage` token buckets, `api_duration` in seconds, `finish_reason`, `message_count`) | cumulative token milestones (Token Tyro/Wizard/Whale), fast-response counting (Speed Demon), `total_tokens` stat, per-request context depth (Deep Context 50 msgs, Context Colossus 100), output-cap truncation (Cut Short 1, Token Wall 25 — `finish_reason="length"` means the model hit its max output tokens and was cut off, a signal usage buckets cannot express) |
| `pre_api_request` | once per provider API request BEFORE it's sent (has `base_url`, `approx_input_tokens`, `api_mode`, `max_tokens`) | local/self-hosted endpoint detection (Local First, Self-Hosted 25), single-request input-token spikes (Context Monster 200K, Token Tsunami 500K) |
| `on_session_start` | new session created | `total_sessions` counter |
| `on_session_end` | end of run_conversation | daily streaks, completions re-check |
| `subagent_stop` | once per delegate_task child (has `child_role`, `child_status`, `duration_ms`) | Army Commander (counts children, not calls), Orchestrator, Resilient, subagent runtime (Slow Thinker 10m, Marathon 60m — how long a child actually ran, a dimension child-counting cannot see) |
| `subagent_start` | once per subagent spawn (has `child_role`, `child_goal`) | true concurrency tracking — live counter + peak (Conductor) |
| `post_approval_response` | user answers an approval prompt (has `choice`: once/session/always/deny/timeout, `surface`: cli/gateway, `pattern_key` + `pattern_keys` — the dangerous-command classes that matched) | Trust Fall, Cautious, YOLO Mode/Champion via "always"; remote-approval dimension (Remote Warden 1 / Long-Distance Operator 10 — `surface="gateway"` means the user approved a dangerous command from a chat platform, bolder than at the CLI); danger-class diversity (Risk Explorer 5 / Danger Collector 15 / Living on the Edge 25 — DISTINCT classes approved, breadth of risk appetite, deduped in a persisted set so repeat approvals of one class add nothing; Under Scrutiny only counts prompt volume) |
| `pre_approval_request` | an approval prompt is raised (has `command`, `surface`, `pattern_key` + `pattern_keys`, `session_key`) | approval-gate counting (Under Scrutiny) — fires BEFORE the user answers, so it measures attempted gates, not consent; the class/surface dimensions live on `post_approval_response` where the choice is known |
| `on_session_reset` | gateway swaps session key (`/new`, `/reset`) | Fresh Start, session-resets counter |
| `on_session_finalize` | agent shutdown / session reset-policy expiry | force-flush debounced state save + synchronously deliver queued notifications (nothing lost on exit) |
| `api_request_error` | LLM provider call fails (has `error_type`, `status_code`, `retry_count`, `max_retries`, `retryable`) | API-error resilience (Indestructible — 10 total errors survived), sustained-failure depth (Tenacious 2 / Undeterred 4 — `retry_count` is how many consecutive times the SAME request failed before the hook fired; breadth≠depth: 10 single failures never reach depth 2). `max_retry_depth` stat |
| `pre_gateway_dispatch` | once per incoming user-originated message (has `event`, `gateway`, `session_store`; event carries `media_urls`/`media_types`/`message_type`, `is_command()`/`get_command()`, `text`, `source`) | distinct-sender counting (Social Butterfly 3 users, Party Host 10), media-message counting (Show and Tell 1, Visual Storyteller 25), gateway-intercepted slash commands (Command Center 10 / Command General 25 — `/new`, `/reset`, `/title`, `/achievements` are intercepted BEFORE the LLM so post_llm_call can never see them; only the platform's PRIMARY user — first non-bot seen = the owner — counts, so strangers' commands in shared channels don't unlock the user's achievements) — the ONLY hook that sees other users' messages |

### Why 18 of Hermes' 19 valid hooks are registered

`transform_llm_output` is deliberately NOT registered. Hermes exposes it
for transforming the final response text (first non-empty string return
wins). The plugin is a strict observer (every transform handler returns
None), and the hook's delivered kwargs (`response_text`, `session_id`,
`model`, `platform`) are a **strict subset** of `post_llm_call`'s
(`assistant_response` + the same session/model/platform), fired under the
identical `if final_response and not interrupted` guard. Registering it
would add zero observability and invite confusion about transform
semantics — so 18/19 is the FINAL hook surface, not an oversight.

### Zero-read hooks are a conscious choice

`check_plugin.py --gateway` lists every registered hook whose handler
reads no kwargs, together with what the gateway delivers — so a
delivered-but-ignored kwarg is visible instead of silently skipped.
Current zero-read hooks and why that's correct:
- `on_session_start` — counter only; its `model`/`platform` are already
  read in `post_llm_call` (identical values, same turn).
- `subagent_start` — live-counter increment (paired with `subagent_stop`
  decrement); `child_role` is read on stop.
- `on_session_reset` / `on_session_finalize` — counters/force-flush;
  their `reason` is always `new_session`/`session_boundary` (single-valued,
  no diversity to observe).
- `pre_approval_request` — gate counter; `surface`/`pattern_keys` are
  read on `post_approval_response` where the choice is known (attempted
  ≠ approved).

## Key invariants

- **Exactly 153 achievements** — `tests/test_plugin.py` enforces this.
- **All achievement IDs must be detectable** — every def needs a path in
  `_TOOL_ACHIEVEMENTS`, `_TOOL_THRESHOLDS`, `TERMINAL_PATTERNS`,
  `_check_tool_args()`, `_check_counter_achievements()`, or an explicit
  `_unlock()` call. Undetectable achievements are bugs.
- **Locale parity** — every def name/description must exist in all 4
  locales with identical key sets (enforced by tests).
- **README sync** — after changing `ACHIEVEMENT_DEFS`, `GROUPS`, the
  group/rarity emoji maps, or any recognition threshold map
  (`_TOOL_THRESHOLDS`, `_TOTAL_TOOL_THRESHOLDS`, `_MESSAGE_THRESHOLDS`,
  `_COUNTER_THRESHOLDS`), run `python3 scripts/render_readme.py` — the
  example-output block's derived numbers come from these too.
- **`turn_id` from Hermes is a string** (`session:task:hex`) — never treat
  it as an int counter. Message counts come from counting `post_llm_call`
  firings.
- **Session-scoped achievements** use `stats.active_session` (id/calls/
  tool_names), which resets when `session_id` changes. Cumulative
  achievements use `stats.tools_used`.
- **State must stay JSON-safe** — sets are persisted as sorted lists and
  normalized back via `_normalize_state()` on load.

## Testing

```bash
python3 -m pytest tests/ -q    # 372 tests, no deps beyond pytest
python3 -m pytest tests/ --cov=. --cov-fail-under=99 -q   # CI coverage gate
ruff check .                   # CI lint gate — must pass before push
```

- **Ruff version drift (recurring CI failure)** — the CI workflow installs
  `"ruff>=0.15.14,<0.17"`, so it runs the LATEST 0.16.x, which enables new
  default rules (e.g. BLE001) that an older local ruff silently passes.
  Always lint with the CI range before push:
  `uv tool run --from "ruff>=0.16,<0.17" ruff check .`
  (v2.18.0 shipped with a red test workflow for exactly this — a bare
  `except Exception` in the new gateway-command handler passed local 0.15.14
  and failed CI's 0.16.1.)

- **Coverage is 100% on `__init__.py`** (99.7% full tree — the only misses
  are inside test files themselves). `tests/test_detection.py::TestCoverageEdges`
  exists purely to close defensive/normalization branches the feature
  suites never reach (list→set state migration, base_url edge cases,
  message_type-only media, badge variants, formatter boundaries, the lock
  double-check, empty-group skip). If you add a branch, add its edge test —
  the CI gate fails below 99%.

- `tests/test_detection.py::TestEveryAchievementUnlockable` — full-grind
  simulation: drives every hook with escalating synthetic gateway data and
  asserts **all 153 defs actually unlock**. This is the enforcement of the
  "every def must be detectable" invariant — after any swap, a dead def
  (impossible threshold, typo'd key, missing path) fails the run with its
  ID listed. Keep the grind's tool/command data broad enough to cover
  every detection map when adding achievements.

## Committing

- Author AND committer must be `omiinaya <omiinaya@gmail.com>`.
- Tests must pass before push. Push immediately — unpushed commits are
  incomplete.
