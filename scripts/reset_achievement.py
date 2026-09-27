#!/usr/bin/env python3
"""Relock a single achievement in the live state.json (undo a test unlock).

Operates under the same flock the plugin uses, and rewrites BOTH state.json
and state.json.bak so a stale backup cannot resurrect the unlock on a
corrupt-state recovery.

The key step is bumping `state_revision`. Without it this edit is silently
undone: the running gateway holds its own in-memory `_state`, and the
plugin's cross-process merge is monotonic by design ("unlocked record
wins" — correct for concurrent counters), so it re-persists the badge a
few seconds after every relock. Bumping the revision tells any live
process that this edit is authoritative; its next save adopts disk
wholesale instead of merging (see `_external_revision` /
`_adopt_disk_wholesale` in __init__.py).

No gateway restart is needed.

Usage:  python3 scripts/reset_achievement.py <ach_id> [--home <hermes_home>]
"""
import argparse
import json
import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _relock(path, ach_id):
    """Relock ach_id in one state file and bump its state_revision."""
    if not os.path.exists(path):
        return "missing"
    with open(path) as f:
        state = json.load(f)
    rec = (state.get("achievements") or {}).get(ach_id)
    if rec is None:
        return "absent"
    was_locked = not rec.get("unlocked")
    state["achievements"][ach_id] = {"unlocked": False}
    newly = state.get("newly_unlocked") or []
    state["newly_unlocked"] = [a for a in newly if a != ach_id]
    # ALWAYS bump, even for an "already-locked" edit: a live process may
    # still hold the badge in memory, and only a newer revision makes it
    # adopt disk instead of re-persisting what it thinks is earned.
    try:
        state["state_revision"] = int(state.get("state_revision") or 0) + 1
    except (TypeError, ValueError):
        state["state_revision"] = 1
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)
    return "already-locked" if was_locked else "relocked"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ach_id")
    ap.add_argument("--home", default=os.environ.get("HERMES_HOME", "/root/.hermes"))
    args = ap.parse_args()

    sys.path.insert(0, PLUGIN_DIR)
    lock_ctx = None
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "achievements_reset_helper", os.path.join(PLUGIN_DIR, "__init__.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lock_ctx = mod._cross_process_lock
    except Exception as exc:  # noqa: BLE001 — fall back to an unlocked edit
        print(f"warning: could not import plugin lock ({exc}); editing unlocked",
              file=sys.stderr)

    base = os.path.join(args.home, "achievements")
    state_path = os.path.join(base, "state.json")
    bak_path = state_path + ".bak"
    if lock_ctx is not None:
        # Same advisory flock the plugin takes, so a concurrent hook save
        # cannot re-persist the badge we are relocking.
        with lock_ctx():
            result = _relock(state_path, args.ach_id)
            _relock(bak_path, args.ach_id)
    else:
        result = _relock(state_path, args.ach_id)
        _relock(bak_path, args.ach_id)

    print(f"{args.ach_id}: {result} (state_revision bumped — no restart needed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
