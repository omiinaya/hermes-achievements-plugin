# AGENTS.md — Hermes Achievements Plugin

## What this is

A Hermes Agent plugin that awards 126 Steam-style achievement badges for
using Hermes. Pure Python stdlib, no external dependencies.

## Repo layout

- `__init__.py` — everything: achievement definitions, detection hooks,
  slash command handlers, state persistence, Discord notifications
- `plugin.yaml` — plugin manifest (name, version, declared hooks)
- `locales/` — en/es/fr/pt JSON translations
- `tests/test_plugin.py` — static validation (defs, locales, file integrity)
- `tests/test_detection.py` — functional tests driving the hooks with
  synthetic gateway kwargs
- `scripts/render_readme.py` — regenerates README achievement tables from
  `ACHIEVEMENT_DEFS`
- `scripts/update_locales.py` — auto-syncs achievement keys across all 4
  locales from `ACHIEVEMENT_DEFS` (source of truth): prunes dead keys,
  backfills missing translations (WARN + English fallback)
- `scripts/bump_version.py` — updates the version in all 4 places that
  carry it (pyproject.toml, plugin.yaml, setup.sh ×2) in one shot
- `scripts/check_plugin.py` — health check: module loads, manifest↔register()
  hook agreement, exactly-126 defs, locale parity, no dead detection-map
  references, live state.json reconciliation (--live), real PluginManager
  load (--manifest), and hook kwarg contract vs the installed Hermes
  source (--gateway — catches silent no-op drift if Hermes renames a
  hook kwarg). Run after any swap:
  `python3 scripts/check_plugin.py --live --manifest --gateway`

## Detection architecture (16 hooks)

| Hook | Fires | Owns |
|------|-------|------|
| `pre_tool_call` | once per tool call BEFORE execution (has `api_request_id` — the ID of the assistant response that emitted the call; every call from one response shares it) | single-response tool-batch counting (Double Time 2, Batch Artist 5, Parallel Barrage 10, Tool Torrent 20) — how many tools the model ran in one step, distinct from cumulative counts and delegate_task parallelism |
| `pre_llm_call` | once per turn BEFORE the LLM is invoked (has `is_first_turn` — True only when run_conversation was handed no prior history) | fresh-conversation counting (Icebreaker 1, Conversation Habit 10, Serial Starter 50, Conversation Colossus 100) — the only signal that counts natural context starts |
| `post_tool_call` | every tool execution (has `tool_name`, `args`, `session_id`, `duration_ms`, `status` ok/cancelled/block/error, `error_type`) | per-tool counts, per-session tracking, argument-based achievements (cron chaining, parallel delegation, skill/plugin authoring), Quick Draw, tool-error counting (Trial and Error — 25 failed calls) |
| `post_llm_call` | once per turn | cumulative message counts, model/platform diversity, user-command patterns, tiered counters, group/rarity completions, message verbosity (Wordsmith 300 words, Novelist 1500) |
| `post_api_request` | once per successful provider API request (has `usage` token buckets, `api_duration` in seconds, `finish_reason`, `message_count`) | cumulative token milestones (Token Tyro/Wizard/Whale), fast-response counting (Speed Demon), `total_tokens` stat, per-request context depth (Deep Context 50 msgs, Context Colossus 100) |
| `pre_api_request` | once per provider API request BEFORE it's sent (has `base_url`, `approx_input_tokens`, `api_mode`, `max_tokens`) | local/self-hosted endpoint detection (Local First, Self-Hosted 25), single-request input-token spikes (Context Monster 200K, Token Tsunami 500K) |
| `on_session_start` | new session created | `total_sessions` counter |
| `on_session_end` | end of run_conversation | daily streaks, completions re-check |
| `subagent_stop` | once per delegate_task child (has `child_role`, `child_status`, `duration_ms`) | Army Commander (counts children, not calls), Orchestrator, Resilient |
| `subagent_start` | once per subagent spawn (has `child_role`, `child_goal`) | true concurrency tracking — live counter + peak (Conductor) |
| `post_approval_response` | user answers an approval prompt (has `choice`: once/session/always/deny/timeout) | Trust Fall, Cautious, YOLO Mode/Champion via "always" |
| `pre_approval_request` | an approval prompt is raised (has `command`, `surface`) | approval-gate counting (Under Scrutiny) |
| `on_session_reset` | gateway swaps session key (`/new`, `/reset`) | Fresh Start, session-resets counter |
| `on_session_finalize` | agent shutdown / session reset-policy expiry | force-flush debounced state save + synchronously deliver queued notifications (nothing lost on exit) |
| `api_request_error` | LLM provider call fails (has `error_type`, `status_code`, `retry_count`) | API-error resilience (Indestructible) |
| `pre_gateway_dispatch` | once per incoming user-originated message (has `event`, `gateway`, `session_store`; event carries `media_urls`/`media_types`/`message_type`) | distinct-sender counting (Social Butterfly 3 users, Party Host 10), media-message counting (Show and Tell 1, Visual Storyteller 25) — the ONLY hook that sees other users' messages |

## Key invariants

- **Exactly 126 achievements** — `tests/test_plugin.py` enforces this.
- **All achievement IDs must be detectable** — every def needs a path in
  `_TOOL_ACHIEVEMENTS`, `_TOOL_THRESHOLDS`, `TERMINAL_PATTERNS`,
  `_check_tool_args()`, `_check_counter_achievements()`, or an explicit
  `_unlock()` call. Undetectable achievements are bugs.
- **Locale parity** — every def name/description must exist in all 4
  locales with identical key sets (enforced by tests).
- **README sync** — after changing `ACHIEVEMENT_DEFS`, run
  `python3 scripts/render_readme.py`.
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
python3 -m pytest tests/ -q    # 280 tests, no deps beyond pytest
```

- `tests/test_detection.py::TestEveryAchievementUnlockable` — full-grind
  simulation: drives every hook with escalating synthetic gateway data and
  asserts **all 126 defs actually unlock**. This is the enforcement of the
  "every def must be detectable" invariant — after any swap, a dead def
  (impossible threshold, typo'd key, missing path) fails the run with its
  ID listed. Keep the grind's tool/command data broad enough to cover
  every detection map when adding achievements.

## Committing

- Author AND committer must be `omiinaya <omiinaya@gmail.com>`.
- Tests must pass before push. Push immediately — unpushed commits are
  incomplete.
