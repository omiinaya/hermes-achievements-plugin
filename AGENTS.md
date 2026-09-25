# AGENTS.md — Hermes Achievements Plugin

## What this is

A Hermes Agent plugin that awards 166 Steam-style achievement badges for
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
- **Wheel packaging (pyproject.toml)** — the wheel ships the plugin as the
  `achievements` package (repo root mapped via `package-dir`), NOT a bare
  top-level `__init__` module (that name collides with Python package
  machinery during PluginManager discovery — `sys.modules['__init__']` gets
  hijacked by another package's `__init__.py`). The pip path requires the
  `[project.entry-points."hermes_agent.plugins"]` declaration; without it
  `pip install` lands files in site-packages but Hermes never discovers the
  plugin. Data files (locales, plugin.yaml) install flattened to
  `<sys.prefix>/achievements/` — see `_WHEEL_DATA_DIR`. Regression-guarded
  by `tests/test_plugin.py::test_wheel_ships_entry_point_for_pip_discovery`.
- `scripts/check_plugin.py` — health check: module loads, manifest↔register()
  hook agreement, exactly-166 defs, locale parity, no dead detection-map
  references, live state.json reconciliation (--live), real PluginManager
  load (--manifest), and hook kwarg contract vs the installed Hermes
  source (--gateway — catches silent no-op drift if Hermes renames a
  hook kwarg). Run after any swap:
  `python3 scripts/check_plugin.py --live --manifest --gateway`
  Under mutation testing, mutmut instruments `__init__.py` (mangles
  function names AND string literals, injects `MutantDict`); check_plugin
  detects that and skips source-text checks (they'd false-fail on the
  trampoline-mangled copy) while runtime checks still run.
- `scripts/bench_hooks.py` — per-hook latency benchmark with realistic
  payloads (1MB terminal output, 10MB tool result, 1500-word responses).
  Catches performance regressions coverage can't: a hook that adds 50ms
  to every tool call is invisible to tests. `python3 scripts/bench_hooks.py`
  Threshold: >2ms on a high-frequency hook is a regression candidate.
- **Mutation testing** — `uvx mutmut run` (see `[tool.mutmut]` in
  pyproject: mutates only `__init__.py`, runs `tests/`). mutate coverage
  beyond line/branch: a 100%-covered module can still host surviving
  mutants (weakened asserts, off-by-one thresholds). `mutants/` is
  gitignored and excluded from pytest via `testpaths = ["tests"]`.

## Detection architecture (19 hooks)

| Hook | Fires | Owns |
|------|-------|------|
| `pre_tool_call` | once per tool call BEFORE execution (has `api_request_id` — the ID of the assistant response that emitted the call; every call from one response shares it) | single-response tool-batch counting (Double Time 2, Batch Artist 5, Parallel Barrage 10, Tool Torrent 20) — how many tools the model ran in one step, distinct from cumulative counts and delegate_task parallelism |
| `transform_terminal_output` | per terminal command with the FULL raw output BEFORE the tool truncates it (has `output`, `returncode`, `env_type` local/ssh/docker/singularity/modal/daytona) | raw output volume (Verbose Output 100KB, Data Flood 1MB) — the only hook that sees what the model was NOT handed; env diversity (Multi-Environment 2, Omnipresent 5); exit code 127 (Ghost Command). TRANSFORM hook — observer returns None, never a string |
| `pre_llm_call` | once per turn BEFORE the LLM is invoked (has `is_first_turn` — True only when run_conversation was handed no prior history) | fresh-conversation counting (Icebreaker 1, Conversation Habit 10, Serial Starter 50, Conversation Colossus 100) — the only signal that counts natural context starts |
| `transform_tool_result` | per tool call with the FULL result string (has `result`, `api_request_id`, `error_message`) | result-size / context bloat (Big Haul 1MB, Colossal Result 10MB) — post_tool_call only gets status, never the content. TRANSFORM hook — observer returns None, never a string |
| `transform_llm_output` | once per turn AFTER the tool-calling loop, BEFORE other plugins transform the reply (has `response_text` — the pre-rewrite final text, `session_id`, `model`, `platform`) | output-rewrite detection — paired with post_llm_call (which fires AFTER the transform loop): if `post_llm_call.assistant_response` != the pre-transform `response_text` seen here, another plugin rewrote the model's output (Remixed Output achievement). TRANSFORM hook — observer returns None, never a string |
| `post_tool_call` | every tool execution (has `tool_name`, `args`, `session_id`, `duration_ms`, `status` ok/cancelled/blocked/error, `error_type`) | per-tool counts, per-session tracking, argument-based achievements (cron chaining, parallel delegation, skill/plugin authoring), Quick Draw, tool-status counting (Trial and Error — 25 failed calls; Manual Override 1 / Backseat Driver 5 / Control Freak 15 — user interrupts, `status="cancelled"` with `error_type` keyboard_interrupt, the user pressing stop mid-tool; Dead End 1 / Brick Wall 10 — policy blocks, `status="blocked"` when scope/plugin/guardrail policy denies the tool BEFORE it runs). Distinct from approvals (consent prompts the user answers) — an interrupt is active user control, a block is environmental policy |
| `post_llm_call` | once per turn (has `assistant_response` — the model's own output text) | cumulative message counts, model/platform diversity, user-command patterns, tiered counters, group/rarity completions, message verbosity (Wordsmith 300 words, Novelist 1500), model-response verbosity (Essayist 1000 words, Novel Author 5000 — a mirror dimension measuring what the MODEL wrote, distinct from user input) |
| `post_api_request` | once per successful provider API request (has `usage` token buckets, `api_duration` in seconds, `finish_reason`, `message_count`) | cumulative token milestones (Token Tyro/Wizard/Whale), fast-response counting (Speed Demon), `total_tokens` stat, per-request context depth (Deep Context 50 msgs, Context Colossus 100), output-cap truncation (Cut Short 1, Token Wall 25 — `finish_reason="length"` means the model hit its max output tokens and was cut off, a signal usage buckets cannot express) |
| `pre_api_request` | once per provider API request BEFORE it's sent (has `base_url`, `approx_input_tokens`, `api_mode`, `max_tokens`) | local/self-hosted endpoint detection (Local First, Self-Hosted 25), single-request input-token spikes (Context Monster 200K, Token Tsunami 500K) |
| `on_session_start` | new session created | `total_sessions` counter (idempotent per `session_id` — a re-delivered session start never double-counts; `model`/`platform` deliberately ignored: a session with no LLM call has no usage to record) |
| `on_session_end` | end of run_conversation | daily streaks, completions re-check |
| `subagent_stop` | once per delegate_task child (has `child_role`, `child_status`, `duration_ms`) | Army Commander (counts children, not calls), Orchestrator, Resilient, subagent runtime (Slow Thinker 10m, Marathon 60m — how long a child actually ran, a dimension child-counting cannot see) |
| `subagent_start` | once per subagent spawn (has `child_role`, `child_goal`) | true concurrency tracking — live counter + peak (Conductor) |
| `post_approval_response` | user answers an approval prompt (has `choice`: once/session/always/deny/timeout, `surface`: cli/gateway, `pattern_key` + `pattern_keys` — the dangerous-command classes that matched) | Trust Fall, Cautious, YOLO Mode/Champion via "always"; remote-approval dimension (Remote Warden 1 / Long-Distance Operator 10 — `surface="gateway"` means the user approved a dangerous command from a chat platform, bolder than at the CLI); danger-class diversity (Risk Explorer 5 / Danger Collector 15 / Living on the Edge 25 — DISTINCT classes approved, breadth of risk appetite, deduped in a persisted set so repeat approvals of one class add nothing; Under Scrutiny only counts prompt volume); timeout ladder (Ghosted 1 / Silent Treatment 5 — `choice="timeout"` means the user never answered; the gateway explicitly normalizes unresolved prompts to "timeout" so plugins can distinguish abandonment from an explicit deny (Cautious)) |
| `pre_approval_request` | an approval prompt is raised (has `command`, `surface`, `pattern_key` + `pattern_keys`, `session_key`) | approval-gate counting (Under Scrutiny) — fires BEFORE the user answers, so it measures attempted gates, not consent; exposure dimension (Watchlisted 5 / Person of Interest 15 / Most Wanted 25 — DISTINCT danger classes the user was PROMPTED to vet, regardless of answer: denied/timeout prompts still count as exposure, distinct from the approved-classes ladder which needs a positive answer); the choice-dependent surface/approved-class dimensions live on `post_approval_response` |
| `on_session_reset` | gateway swaps session key (`/new`, `/reset`) | Fresh Start, session-resets counter |
| `on_session_finalize` | agent shutdown / session reset-policy expiry | force-flush debounced state save + synchronously deliver queued notifications (nothing lost on exit) |
| `api_request_error` | LLM provider call fails (has `error_type`, `status_code`, `retry_count`, `max_retries`, `retryable`) | API-error resilience (Indestructible — 10 total errors survived), sustained-failure depth (Tenacious 2 / Undeterred 4 — `retry_count` is how many consecutive times the SAME request failed before the hook fired; breadth≠depth: 10 single failures never reach depth 2). `max_retry_depth` stat |
| `pre_gateway_dispatch` | once per incoming user-originated message (has `event`, `gateway`, `session_store`; event carries `media_urls`/`media_types`/`message_type`, `is_command()`/`get_command()`, `text`, `source`) | distinct-sender counting (Social Butterfly 3 users, Party Host 10), media-message counting (Show and Tell 1, Visual Storyteller 25), gateway-intercepted slash commands (Command Center 10 / Command General 25 — `/new`, `/reset`, `/title`, `/achievements` are intercepted BEFORE the LLM so post_llm_call can never see them; only the platform's PRIMARY user — first non-bot seen = the owner — counts, so strangers' commands in shared channels don't unlock the user's achievements) — the ONLY hook that sees other users' messages |

### Why all 19 of Hermes' valid hooks are registered

Every one of Hermes' 19 valid hooks is registered. `transform_llm_output`
fired **before** the transform loop (gateway hands every observer the
pre-rewrite `response_text`, then applies the first non-None string
another plugin returns), while `post_llm_call` fires **after** it with
`assistant_response`. The plugin is a strict observer (every transform
handler returns None), and by comparing the two it detects when **another
plugin rewrote the model's output** before delivery — the `remixed_output`
achievement, a dimension raw response-length cannot see. The kwargs look
like a subset of `post_llm_call`'s, but the *timing* carries the signal.

### Thread safety is mandatory (gateway runs hooks on worker threads)

Hermes executes parallel tool calls on worker threads
(`execute_tool_calls_concurrent` → `propagate_context_to_thread`), so
hook callbacks can fire concurrently on different threads. Every
registered hook and command handler MUST be wrapped in `_synchronized`
at registration time (it acquires `_state_lock` — an RLock, because
`_save_state`/`_load_state` re-acquire it internally). An unwrapped
handler risks lost updates on read-modify-write counters
(`tools_used[x] = tools_used[x] + 1`) and racing check-then-act unlock
sequences. Enforced by
`tests/test_detection.py::TestPluginRegistration::test_all_registered_handlers_are_synchronized`
and the concurrency stress test (`test_concurrent_hook_calls_do_not_lose_updates`).
Module globals like `_current_batch` are safe ONLY because every access
sits inside a wrapped hook body — keep it that way. Lock ordering:
hooks take `_state_lock` → `_NOTIF_QUEUE_LOCK` (never the reverse); the
notification timer releases its queue lock before any state access.

### Session context is ContextVar, not os.environ

The gateway stores per-message routing state (`HERMES_SESSION_CHAT_ID`,
`HERMES_SESSION_PLATFORM`, etc.) in task-local ContextVars
(`gateway/session_context.py`), NOT `os.environ` — it migrated because
process-global env values were clobbered by concurrent messages. Reading
`os.environ.get("HERMES_SESSION_CHAT_ID", "")` in a hook returns `""` in
gateway contexts: origin notifications silently never sent (v2.18.9).
Also, a debounced Timer thread has no session context at all, so capture
session-scoped values at hook time (inside the session's context) and
carry them through any queue. Use the ContextVar-aware accessor with an
`os.environ` fallback for CLI/cron/tests:

```python
def _session_chat_id():
    try:
        from gateway.session_context import get_session_env
        return get_session_env("HERMES_SESSION_CHAT_ID", "")
    except Exception:
        return os.environ.get("HERMES_SESSION_CHAT_ID", "")
```

### Delivered-but-unread kwargs are a conscious choice

`check_plugin.py --gateway` lists every registered hook whose handler
ignores at least one delivered kwarg (and zero-read hooks in full),
together with what the gateway delivers — so a delivered-but-ignored
kwarg is visible instead of silently skipped.
Current unread kwargs and why that's correct:
- `on_session_start` — reads `session_id` for idempotent counting; its
  `model`/`platform` are deliberately NOT read — they're already persisted
  by `post_llm_call`/`on_session_end` (identical values), and a session
  that never reaches the LLM has no model usage to record.
- `subagent_start` — live-counter increment (paired with `subagent_stop`
  decrement); `child_role` is read on stop.
- `on_session_reset` / `on_session_finalize` — counters/force-flush;
  their `reason` is always `new_session`/`session_boundary` (single-valued,
  no diversity to observe).
- `pre_approval_request` — gate counter + exposure breadth; `pattern_keys`
  are read here (before the user answers) for the exposure ladder
  (Watchlisted/Person of Interest/Most Wanted — distinct classes PROMPTED,
  regardless of answer), while `surface`/approved-classes are read on
  `post_approval_response` where the choice is known (attempted ≠
  approved).

## Key invariants

- **Exactly 166 achievements** — `tests/test_plugin.py` enforces this.
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
python3 -m pytest tests/ -q    # 395 tests, no deps beyond pytest
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

- **GitHub Actions runner-provisioning incidents** — when the status page
  says operational but every workflow run fails in <15s with ZERO steps
  (jobs created, `runner_id=0`, never allocated), it's an unlisted
  infrastructure incident, not your code. Corroborate by checking other
  repos' latest runs (`gh api repos/<owner>/<repo>/actions/runs?per_page=1`).
  v2.18.4 hit this on 2026-08-01 (6+ repos affected). Fallback that ships
  the release without CI (only when the tag-push workflow cannot run):
  ```
  git worktree add /tmp/ach-release v<tag>          # exact released tree
  cd /tmp/ach-release && python3 -m pytest tests/ -q && python3 -m build --wheel
  # run the wheel-content verification inline check from release.yml,
  # then the "Wheel install smoke test" step's inline script (pip install
  # dist/*.whl into a clean venv; assert the entry point loads and all 4
  # locales resolve with real translations — this is what caught the
  # v2.18.5 locale regression and the v2.18.6 entry-point absence),
  # extract the CHANGELOG notes the same way, then:
  gh release create v<tag> --title v<tag> --notes-file release_notes.md dist/*.whl
  ```
  Never do this for a tag whose workflow run is merely RED — only for
  runs that never STARTED (zero steps).

- **Coverage is 100% line AND 100% branch on `__init__.py`** (99% full
  tree — the only misses are inside test files themselves).
  `tests/test_detection.py::TestCoverageEdges` +
  `TestBranchCoverageComplete` exist purely to close defensive/normalization
  branches the feature suites never reach (list→set state migration, base_url
  edge cases, message_type-only media, badge variants, formatter boundaries,
  the lock double-check, empty-group skip, corrupt-state-without-backup
  recovery, stale achievement ids, legacy list-typed stats, unknown
  models/providers, non-dict usage). If you add a branch, add its edge test —
  the CI gate fails below 99% and branch coverage is now tracked locally
  with `--cov-branch`.

- `tests/test_detection.py::TestEveryAchievementUnlockable` — full-grind
  simulation: drives every hook with escalating synthetic gateway data and
  asserts **all 166 defs actually unlock**. This is the enforcement of the
  "every def must be detectable" invariant — after any swap, a dead def
  (impossible threshold, typo'd key, missing path) fails the run with its
  ID listed. Keep the grind's tool/command data broad enough to cover
  every detection map when adding achievements.

## Committing

- Author AND committer must be `omiinaya <omiinaya@gmail.com>`.
- Tests must pass before push. Push immediately — unpushed commits are
  incomplete.
