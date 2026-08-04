# Changelog

## [2.21.1] — 2026-08-03

### Security & privacy hardening (production-readiness audit)

- **State file is now written owner-only (0600)** regardless of umask.
  `state.json` holds personal data (platform user IDs, usage metadata); the
  previous `open(path, "w")` honored umask (0644/0666 on shared boxes),
  leaving it world-readable. The atomic temp+replace now creates the temp
  with `os.open(..., 0o600)` so the final file is always owner-only.
  Regression test: `test_state_file_written_owner_only`.
- **Slash-command parsers no longer record paths as commands.** The
  `pre_gateway_dispatch` text-fallback and the `post_llm_call` LLM-path
  parser both accepted ANY `/`-prefixed token — a user message like
  `/tmp/foo.log` or a markdown code span (`/tmp/x\``) was recorded as a
  "slash command" in `slash_commands_used` (observed live: `tmp/userdata
  _full5.log`). Both now validate against `_is_plausible_command()`
  (alnum/hyphen/underscore, ≤32 chars) and skip path/URL/code-span tokens.
  New tests: `test_fallback_path_token_is_not_a_command`,
  `test_fallback_backtick_code_span_is_not_a_command`,
  `test_llm_path_token_is_not_a_command`,
  `test_real_commands_still_count_via_fallback`.
- **README Privacy section** — documents exactly what is stored locally
  (counters/metadata + platform user IDs), what is never stored
  (message content, terminal output, tool results, args, credentials),
  and that the only network egress is the Discord notification POST.

### New in this release

- **454 tests, 100% line + 100% branch** (5 new).

## [2.21.0] — 2026-08-02

### New achievement

- **Bait and Switch** (uncommon, Power User) — unlock when a provider
  resolves a different model than the agent requested (alias / proxy
  rewrite / fallback). `post_api_request` delivers both `model` (what
  was asked) and `response_model` (what actually served) — a dimension
  the requested-model-only ladder (Model Hopper etc.) cannot observe.
  Tracks distinct `(requested → resolved)` pairs in a `model_switches`
  stat. The gateway's kwarg contract confirms both `model` and
  `response_model` are delivered on every successful provider call.

### Fixes

- Ruff 0.16 lint fixes for 7 pre-existing errors (BLE001 ×2, EXE001,
  RUF100, FURB167, C408, UP017) that were failing CI on main since
  v2.20.0.

### New in this release

- 1 new achievement → **160 total**; Power User 45 → 46.
- Stats UI line for model switches (all 4 locales).
- Legacy-list render test for `model_switches` → 100% branch coverage.
- **449 tests, 100% line + 100% branch** (1468 stmts, 714 branches).

## [2.20.0] — 2026-08-02

### New achievements

The approval dimension had two blind spots against the real gateway
source (`tools/approval.py`):

- **Ghosted** (uncommon) / **Silent Treatment** (rare) — the gateway
  explicitly normalizes an unanswered approval prompt to
  `choice="timeout"` ("report that explicitly so plugins can distinguish
  timeout from explicit deny"), and the plugin's own docstring documented
  `timeout` as a valid choice — but no handler branch existed. An
  abandoned prompt (user walked away, approval window expired) is a
  DIFFERENT behavior from an explicit deny (Cautious), so it now drives
  its own ladder via `approvals_timed_out`.
- **Watchlisted** (uncommon) / **Person of Interest** (rare) / **Most
  Wanted** (epic) — `pre_approval_request` reads zero kwargs even though
  the gateway delivers `pattern_keys` at REQUEST time (before the user
  answers). The plugin now tracks `exposed_patterns`: distinct danger
  classes the user was PROMPTED to vet, regardless of whether they
  approved, denied, or timed out. This is distinct from the approved-
  classes ladder (risk_explorer/danger_collector/living_on_the_edge)
  which only counts positive answers — a user who denies everything still
  accumulates exposure. `exposed_patterns` and `approved_patterns` are
  independent sets.

### New in this release

- 6 new achievements → **160 total**; Power User 45 → 46.
- New stats UI lines: danger classes reviewed, approvals unanswered.
- 12 new tests (Timeouts + Exposure classes): first/fifth timeout unlock,
  deny≠timeout, absent-choice safe, exposure cascades to 25, repeated
  class deduped, multi-key counting, denied-classes still count as
  exposure, exposure≠approved independence, absent pattern_keys safe,
  persisted-list normalization.
- Full-grind now fires timeouts + exposure + model switches so all 160 defs unlock.
- **449 tests, 100% line + 100% branch** (1468 stmts, 714 branches).
- Gateway-contract check now validates `pre_approval_request` delivers
  `pattern_keys` (39 keys across 16 hooks).

## [2.19.1] — 2026-08-02

### New achievement

- **Remixed Output** (rare, Power User) — unlock when another plugin
  rewrote the model's response before delivery. Hermes exposes 19 valid
  hooks; the plugin had registered 18. `transform_llm_output` fires
  AFTER the tool-calling loop but BEFORE the transform loop — every
  observer receives the pre-rewrite `response_text`, then the gateway
  applies the first non-None string any other plugin returned. By
  comparing that pre-transform text (stashed per-session in a transient
  bridge) with `post_llm_call`'s `assistant_response` (post-rewrite),
  the plugin detects when the user saw output a plugin produced rather
  than the model's own words — a dimension none of the raw-response-
  length achievements can express. Also tracks an `outputs_transformed`
  counter. The naive interpretation ("kwargs are a subset of
  post_llm_call's → zero observability") was wrong: the *timing*
  relative to other plugins carries the signal. Registered as a strict
  observer (always returns None).
- Guarded the false-unlock: a `transform_llm_output` firing with no
  text must not make the next ordinary reply look remixed.

### New in this release

- Remixed Output and its edge cases covered (6 new tests): unchanged
  reply no unlock, rewritten reply unlocks, no cross-session leakage,
  per-turn pending consumption, non-string response ignored, empty
  pre-transform never false-unlocks.
- **429 tests, 100% line + 100% branch** (1433 stmts, 690 branches).
- All 19 valid Hermes hooks are now registered; gateway-contract check
  validates `transform_llm_output` delivers `response_text`/`session_id`.

## [2.19.0] — 2026-08-02

### Performance

- **`transform_tool_result` 5.3× faster on large results.** The hook did
  `len(result.encode("utf-8"))` on EVERY tool result, allocating a full
  copy of the string — 24ms for a 10MB result, paid on every call. New
  `_measure_utf8_bytes()` helper: UTF-8 is ≤4 bytes/char, so when
  `len(s)*4 < threshold` the byte count provably can't reach the
  achievement threshold and the char count is returned without any
  allocation; the encode only runs when the threshold could actually be
  crossed. 24,009 µs → 4,564 µs (10MB), small results ~5 µs.
- **`transform_terminal_output` same fix** — the 1MB output path no
  longer allocates a copy when below threshold.
- **`_save_state` 2.4× faster (post_llm_call).** The old code did a full
  JSON round-trip (`dumps` → `loads` → `dumps`) just to turn sets into
  sorted lists, on every force save — and `post_llm_call` force-saves
  once per turn. Single-pass serialization with a combined `default`
  (sets → sorted, everything else → str) eliminates both wasted passes:
  12,827 µs → 5,338 µs per turn. Same durability: atomic temp+replace,
  fsync, rolling backup preserved.
- **New `scripts/bench_hooks.py`** — per-hook latency benchmark with
  realistic payloads (1MB output, 10MB result, 1500-word responses) so
  performance regressions are measurable, not invisible.

### Mutation testing

- **mutmut now actually runs.** Four harness fixes:
  - `testpaths = ["tests"]` so the `mutants/` working copies are never
    collected by plain pytest runs
  - `check_plugin.py` detects mutmut-instrumented source
    (`_mutmut_mutated` / `MutantDict` markers — function names AND string
    literals get mangled) and skips ALL source-text checks (manifest↔
    register, wrapper regex, session-env regex, kwarg contract) that
    would false-fail on the trampoline-instrumented copy; runtime checks
    still run and count
  - `[tool.mutmut] source_paths` must be a **list** — a bare TOML string
    is iterated char-by-char by mutmut's config reader (Path('_'),
    Path('_')...), which mutated 36 files across the tree incl. .venv;
    plus `also_copy` lists every repo file the tests touch (locales,
    scripts, plugin.yaml, setup.sh, README, CHANGELOG, LICENSE,
    .gitignore) beyond mutmut's default (tests/, pyproject, uv.lock)
  - the test harness registers the module as `__init__` when running
    from a mutmut copy so the trampoline key matches what mutmut derives
    from the file path — otherwise every mutant is silently marked
    "No Tests"
- **Known limitation:** mutmut's per-mutant coverage-based test
  selection reports "no tests" for every mutant because the plugin is
  loaded via `spec_from_file_location` from a temp dir, which mutmut's
  coverage tracer can't attribute back to tests. The harness runs, the
  baseline is green, mutations are generated correctly from exactly
  `__init__.py`; per-mutant kill/survive classification needs a
  different mutation runner (e.g. pytest-mutagen or manual mutant
  injection) if that granularity becomes a priority.

### Coverage

- **421 tests, 100% line + 100% branch coverage** (1420 stmts, 686
  branches, 0 missed). Closed the last gaps:
  - `_measure_utf8_bytes` boundary behavior (fast path vs exact path,
    4-byte chars at threshold, empty string)
  - Early Bird / Night Owl hour branches — the suite only exercised the
    True paths at test time; hour is now mocked for both sides
  - `_get_session_env` fallback when the gateway module exists but
    `get_session_env` raises
  - `_convert` stringifying non-set non-JSON values (datetimes in state)

## [2.18.9] — 2026-08-02

### Fixed

- **Origin notifications actually send from the gateway now.** The gateway
  stores `HERMES_SESSION_CHAT_ID` in a task-local `ContextVar`
  (`gateway/session_context.py`), not in `os.environ` — it migrated away
  from process-global env vars because concurrent messages clobbered each
  other. The notification batch read `os.environ.get(...)`, which is
  always `""` in gateway contexts: origin-channel notifications silently
  never sent (only the home channel worked). The lookup now goes through
  the ContextVar-aware `get_session_env()` when the gateway package is
  importable, with the legacy `os.environ` fallback for CLI/cron.
- **Origin is captured at enqueue time, not flush time.** The debounce
  flush runs 3s later in a Timer thread whose context has no session
  vars — reading the channel there would always miss (or hit a stale
  value). Each queued notification now carries the origin captured inside
  the hook's session context, and a debounce-window burst that spans
  sessions sends one message to each distinct origin (deduped against the
  home channel; non-numeric platform IDs still skipped as bogus Discord
  channels).

## [2.18.8] — 2026-08-02

### Fixed

- **Hook state mutations are now serialized.** The gateway executes
  parallel tool calls on worker threads (`execute_tool_calls_concurrent`
  → `propagate_context_to_thread`), so hooks can fire concurrently on
  different threads. The shared in-memory `_state` dict was mutated
  without any lock: read-modify-write counters like
  `tools_used[x] = tools_used[x] + 1` lost updates (reproduced: 197/200
  calls recorded under contention) and check-then-act unlock sequences
  could race. All 18 hooks + 2 command handlers are now wrapped in a
  re-entrant `_state_lock` at registration time (`_synchronized`), so a
  hook body is atomic with respect to every other hook body and every
  state save/load. The lock is re-entrant because `_save_state` /
  `_load_state` acquire it internally. Verified end-to-end from a
  pip-installed wheel: 8 threads × 25 calls → exactly 200 recorded,
  zero errors. Covered by a concurrency regression test that runs the
  registered handlers from a thread barrier.

## [2.18.7] — 2026-08-02

### Fixed

- **State saves are now atomic.** `_save_state` previously wrote
  `state.json` with `open(path, "w")` — truncate-in-place, then write. A
  crash, kill, or concurrent session mid-write left a torn/truncated
  state file that only the rolling backup could recover (and the backup
  copy itself raced the same write). The save now writes to
  `state.json.tmp`, fsyncs, then `os.replace()`s it into place — atomic
  on POSIX, so a reader (or a second gateway process) can only ever see
  the previous complete state or the new complete state, never a partial
  one. A half-written temp is removed on failure so no residue
  accumulates. Covered by four new regression tests, including one that
  simulates a crash mid-write and asserts the previous state survives
  byte-for-byte.

## [2.18.6] — 2026-08-02

### Fixed

- **`pip install` never actually worked as a plugin install path.** The
  wheel shipped the plugin as a top-level `__init__` module with no
  entry point — Hermes discovers pip-installed plugins via
  importlib.metadata entry points in group `hermes_agent.plugins`, and
  even if one had been declared, a bare `__init__` module name collides
  with Python package machinery during PluginManager discovery
  (`sys.modules['__init__']` gets hijacked by another package's
  `__init__.py`, so `register()` is never found). The wheel now ships
  the code as a proper `achievements` package (repo root mapped via
  `package-dir`) and declares `[project.entry-points."hermes_agent.plugins"]`
  `achievements = "achievements"`. Verified end-to-end: clean venv +
  `pip install` + real `PluginManager.discover_and_load()` → plugin
  found with source=`entrypoint`, 18 hooks + 2 commands registered,
  all 4 locales load.

### Tests

- New `test_wheel_ships_entry_point_for_pip_discovery` — pins the
  entry-point group, name, and value in pyproject.toml so the pip path
  can never silently rot again.
- `TestBranchCoverageComplete` (17 tests) — branch coverage on the
  module is now **100%** (680/680), up from 99% with 20 partial
  branches: corrupt-state-without-backup recovery, non-JSON locale
  files, empty locale-dir fallthrough, unknown ach-id unlock, empty
  tool diversity, plugin.yaml without manifest content, register_hook
  edge cases, unknown model/provider, non-string history content,
  non-dict usage, stale achievement ids, empty stats, legacy list-typed
  stats.
- CI gains a dedicated **branch coverage gate** (`--cov-branch
  --cov-fail-under=100` on the module alone — the full tree sits at
  99% only because test files carry their own branches, which is
  circular).
- pytest-asyncio is now disabled via `addopts = "-p no:asyncio"` —
  silences its deprecation warning locally and is a no-op in CI (which
  never installs it).
- Suite verified on the full CI Python matrix: 3.11, 3.12, 3.13 (396
  tests each).

## [2.18.5] — 2026-08-01

### Fixed

- **Wheel-installed plugin lost its locales (English-only fallback)** —
  `_WHEEL_DATA_DIR` guessed `<site-packages>/achievements/locales`, but
  setuptools data-files are prefix-relative and FLATTENED: they actually
  install to `<sys.prefix>/achievements/` (en.json directly, no locales
  subdir). A pip-installed copy silently fell back to English-only — the
  repo-checkout path (HERMES_HOME) hid the bug because it's tried first.
  Found by installing the shipped v2.18.4 wheel into a clean venv and
  smoke-testing it. The wheel path is now `sys.prefix/achievements/`.
- Stale module docstring count (108 → 153).

### Tests

- New `test_wheel_data_dir_default_points_at_sys_prefix` — pins the
  default computation so the path can never silently rot again (the old
  fallback test only exercised the lookup LOGIC with a hand-set dir, so
  it never saw the wrong default).

## [2.18.4] — 2026-08-01

### Changed

- **`_handle_next_up` / `_next_up_hint` DRY'd** — the one-line teaser and
  the full next-up view previously ran two near-identical candidate loops
  that could disagree: on a progress-percentage TIE the hint picked the
  achievement defined first, while the view broke ties by internal id
  string. Both now share `_next_up_candidates()` with a stable sort
  (ties keep `ACHIEVEMENT_DEFS` order), so the hint and the top of the
  view always name the same achievement. Behavior is unchanged for
  non-tied progress.

### Tests

- New `test_next_up_hint_and_view_agree_on_pct_ties` — two achievements
  at identical progress must surface in the same order in both views.

## [2.18.3] — 2026-08-01

### Fixed

- **`total_sessions` could inflate on gateway re-delivery** —
  `on_session_start` counted every firing blindly. The gateway delivers
  `session_id` with the event (previously unread), so a re-delivered
  session start (crash-recovery retry, hook double-fire) would inflate
  the counter and unlock **Persistent** (3 sessions) early. The counter
  is now idempotent per `session_id` (`stats.last_session_id`); sessions
  without an id keep the legacy count-every-firing behavior. `model` /
  `platform` on the hook remain deliberately unread — a session that
  never reaches the LLM has no usage to record, and
  `post_llm_call`/`on_session_end` already persist them.

## [2.18.2] — 2026-08-01

### Fixed

- **README example block rotted twice** — the illustrative `/achievements`
  preview under `### Example output` is hand-maintained and nothing
  validated it: group denominators showed `(0/18)` for Expert (now 40),
  `(0/15)` for Milestones (now 19), `(0/23)` Power User (now 44),
  `(2/10)` Getting Started (now 16), and Deep Diver displayed `2/5 (40%)`
  after the def moved to 25 web searches. The renderer now OWNS the whole
  block: group denominators/bars, next-up thresholds/bars/percents and
  the closest-to-unlock hint are all derived from `ACHIEVEMENT_DEFS` +
  the recognition maps, and it fails loudly if a line is missing.
- **`_check_counter_achievements` de-magic-numbered** — 12 hardcoded
  thresholds (config_changes 15, skills_installed 1/5/15, cron_jobs 5/15,
  yolo_tasks 25, …) moved into a literal-evaluable `_COUNTER_THRESHOLDS`
  map. Behavior-preserving, but the thresholds are now introspectable
  (which is what lets the renderer derive the example block) and
  testable.
- Stale `_format_badge` docstring ("Power User is 33" → 44).

### Tests

- `test_render_script_matches_readme` now diff-gates locally (runs the
  renderer and asserts the committed README is unchanged), mirroring the
  CI git-diff gate so drift fails pytest instead of only the workflow.
- New `test_example_block_matches_defs` validates every DERIVED number in
  the example block against the module's real recognition maps.

## [2.18.1] — 2026-08-01

### Fixed

- **Ruff version drift (test workflow was red for v2.18.0)** — the CI
  workflow installs `ruff>=0.15.14,<0.17`, so it runs the latest 0.16.x,
  which enables BLE001 (blind `except Exception`) by default; the local
  0.15.14 did not. The new gateway-command handler's defensive
  `except Exception` passed local lint and failed CI. Added the targeted
  `# noqa: BLE001` and documented the drift in AGENTS.md — lint with
  `uv tool run --from "ruff>=0.16,<0.17" ruff check .` before push.

## [2.18.0] — 2026-08-01

### Added

- **Gateway-command dimension** — slash commands the user types are
  intercepted by the gateway BEFORE the LLM (`/new`, `/reset`, `/title`,
  `/model`, `/achievements` — 56 known commands), so `post_llm_call`
  could never count them: the plugin's own `/achievements` command could
  not unlock Slash Commander. `pre_gateway_dispatch` fires before command
  handling, so `event.get_command()` makes them observable:
  - **Command Center** 🎚️ (rare, Power User) — use 10 different slash
    commands; **Command General** 🎖️ (epic, Power User) at 25.
  - **Primary-user attribution** — the hook fires for ALL users pre-auth
    in shared channels, so a per-platform primary user (the first non-bot
    user seen — the owner in every real deployment) is recorded and only
    THEIR commands count. Strangers' commands cannot unlock the user's
    achievements (tested).
  - **Canonical command form** — `get_command()` returns `title` while
    the LLM path stored `/title`; both now strip the leading slash so the
    same command typed either way dedupes in one set (tested).
  - `slash_commands_used` now feeds Slash Commander from BOTH paths;
    `stats_slash_commands` stat line added. 2 new achievements
    (151 → 153, Power User 42 → 44).

## [2.17.1] — 2026-08-01

### Fixed

- **README architecture prose** — the `post_approval_response` /
  `pre_approval_request` / `api_request_error` descriptions still showed
  the pre-v2.17.0 surface (choice-only, no retry depth). Now document the
  approval-context dimension (surface + danger-class diversity) and
  sustained-failure depth.
- **Discord length regression guard** — `test_all_views_under_discord_limit_all_locales`
  previously unlocked only 50 achievements and zero stats. It now
  exercises the true worst case: all 151 achievements unlocked + every
  stats counter populated, across all 4 locales and all 10 views. Worst
  measured output: 1364 chars (pt stats view) — comfortably under the
  2000-char cap. A stats line added in the future that overflows will
  fail CI instead of silently truncating on Discord.

## [2.17.0] — 2026-08-01

### Added

- **Approval-context dimension** — the plugin was registered on
  `post_approval_response` but read only `choice`, silently ignoring the
  7 other kwargs the gateway delivers. Now reads `surface` and
  `pattern_keys` (the gateway's ~40 dangerous-command classes: rm, chmod,
  mkfs, dd, DROP TABLE, systemctl, kill -9, curl|sh, docker down, git
  push --force, sudo -S...):
  - **Remote Warden** 🛰️ (uncommon, Expert) — approve a dangerous command
    from a chat platform (`surface="gateway"`); **Long-Distance Operator**
    🚁 (rare, Expert) at 10. Approving remotely is bolder than at the CLI —
    a dimension the choice itself cannot express.
  - **Risk Explorer** 🧨 (uncommon, Expert) — approve commands in 5
    distinct danger classes; **Danger Collector** ⚗️ (rare) at 15;
    **Living on the Edge** ☢️ (epic) at 25. DISTINCT classes, deduped in a
    persisted set — approving `rm` 10 times counts as 1, not 10; breadth
    of risk appetite vs Under Scrutiny's total prompt volume.
  - `approvals_gateway` counter + `approved_patterns` set (JSON-safe,
    normalized on load), stats-view lines, es/fr/pt translations. 5 new
    achievements (146 → 151, Expert 35 → 40).

### Fixed

- **check_plugin.py blind spot** — the gateway-contract scan skipped
  registered hooks whose handlers read zero kwargs (`if not keys:
  continue`), which is exactly how `pre_approval_request`'s 6 delivered
  kwargs and `post_approval_response`'s 7 went unread and unnoticed. The
  checker now lists every zero-read hook with what the gateway delivers,
  so a delivered-but-ignored kwarg is visible (34 keys now checked across
  13 hooks; 5 zero-read hooks reported and each justified in AGENTS.md).
- **render_readme.py count drift** — the README header badge count was
  hardcoded ("139") and had silently drifted two releases behind (144 at
  v2.14.0). The script now rewrites the count from `ACHIEVEMENT_DEFS`.

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
