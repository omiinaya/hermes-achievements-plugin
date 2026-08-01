# AGENTS.md — Hermes Achievements Plugin

## What this is

A Hermes Agent plugin that awards 100 Steam-style achievement badges for
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
- `scripts/update_locales.py` — adds/removes achievement keys across all 4
  locales (used when swapping achievements)

## Detection architecture (7 hooks)

| Hook | Fires | Owns |
|------|-------|------|
| `post_tool_call` | every tool execution (has `tool_name`, `args`, `session_id`, `duration_ms`) | per-tool counts, per-session tracking, argument-based achievements (cron chaining, parallel delegation, skill/plugin authoring), Quick Draw |
| `post_llm_call` | once per turn | cumulative message counts, model/platform diversity, user-command patterns, tiered counters, group/rarity completions |
| `on_session_start` | new session created | `total_sessions` counter |
| `on_session_end` | end of run_conversation | daily streaks, completions re-check |
| `subagent_stop` | once per delegate_task child (has `child_role`, `child_status`, `duration_ms`) | Army Commander (counts children, not calls), Orchestrator, Resilient |
| `post_approval_response` | user answers an approval prompt (has `choice`: once/session/always/deny/timeout) | Trust Fall, Cautious, YOLO Mode/Champion via "always" |
| `on_session_reset` | gateway swaps session key (`/new`, `/reset`) | Fresh Start, session-resets counter |

## Key invariants

- **Exactly 100 achievements** — `tests/test_plugin.py` enforces this.
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
python3 -m pytest tests/ -q    # 130 tests, no deps beyond pytest
```

## Committing

- Author AND committer must be `omiinaya <omiinaya@gmail.com>`.
- Tests must pass before push. Push immediately — unpushed commits are
  incomplete.
