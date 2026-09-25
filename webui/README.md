# Hermes Achievements — Steam-style WebUI

A self-contained, dependency-free static site that renders the Hermes
achievements plugin's `state.json` as a Steam-like achievement showcase:
dark gradient slate backdrop, a "HERMES" game banner, a left sidebar with
group/rarity filters + search + sort, a main grid of animated badges (emoji
inside rarity-colored rings), a stat strip, a recently-unlocked ticker, and a
completionist banner on full clear.

No build step, no CDN, no frameworks. Works 100% offline by double-clicking
`index.html` or behind any trivial static server.

## Files

| File            | Purpose                                                    |
|-----------------|------------------------------------------------------------|
| `index.html`    | Single page shell                                          |
| `style.css`     | Dark Steam aesthetic, rarity colors, lock/glow states      |
| `app.js`        | Fetches state, merges defs, renders filters/tiles/stats    |
| `defs.js`       | Achievement static metadata (names/emoji/rarity/group)     |
| `demo/state.json` | Sample state file for preview without live data          |
| `README.md`     | This file                                                  |

> `defs.js` is generated from `ACHIEVEMENT_DEFS` in
> `/root/hermes-achievements-plugin/__init__.py`. If the plugin's definitions
> change, regenerate it (see below). The UI **does not** parse `__init__.py`
> at runtime — definitions are baked into `defs.js` so the page stays offline.

## How to serve

```bash
cd /root/hermes-achievements-plugin/webui
python3 -m http.server 8000
# open http://localhost:8000/
```

Or open `index.html` straight from the filesystem (fetch of `state.json` needs
slightly relaxed CORS, so a server is the recommended path).

### Providing live data

By default the page fetches `state.json` from the same directory as
`index.html`. Give it your live plugin state with a symlink:

```bash
cd /root/hermes-achievements-plugin/webui
ln -s ~/.hermes/achievements/state.json state.json
```

Or point at any path with the query param:

```
http://localhost:8000/?state=/root/.hermes/achievements/state.json
```

If the file is missing/unreadable the UI degrades gracefully and shows
"No achievements yet" with setup hints — it never hard-fails.

### Preview with the demo data

```bash
# put the sample in place, then reload the page
cp webui/demo/state.json webui/state.json
```

(Or `?state=demo/state.json`.)

## state.json contract

Written by the plugin to `$HERMES_HOME/achievements/state.json`
(`HERMES_HOME` defaults to `~/.hermes`). Top-level shape:

```jsonc
{
  "achievements": {
    // unlocked:
    "first_steps": { "unlocked": true, "unlocked_at": "2026-08-01T09:14:00-04:00" },
    // in-progress (locked but has a target):
    "shell_master": { "unlocked": false, "progress": { "current": 78, "target": 100 } }
  },
  "stats": {
    "total_turns": 1247,
    "tools_used": { "terminal": 89, "web_search": 156, /* ... */ },
    "longest_streak": 31, "current_streak": 7, "last_active_date": "2026-09-25",
    // ... many more
  },
  "newly_unlocked": ["speed_demon", "early_bird", "..."],   // most-recent 20 ids
  "locale": "en",
  "last_updated": "2026-09-25T07:15:00-04:00"
}
```

Notes the UI relies on:

- **Achievement IDs** in `state.achievements` must match the ids in
  `defs.js`. Unknown ids are skipped; defs without a state entry render as
  locked.
- **`stats.tools_used`** may be a map `{tool: count}` (persisted) — the UI
  sums its values for the "Tool calls" stat. It also tolerates a list.
- **`newly_unlocked`** feeds the ticker.
- The plugin persists sets as JSON lists (e.g. `platforms`, `models_used`).
- `completionist` unlocked triggers the golden 100% banner.
- `secret` defs render with a `???` name/description and greyscale badge
  while locked.

## Features

- **Group sidebar**: filter by group (`All` + each of the 6 groups), each
  showing `unlocked/total` counts. All known defs land under a matching group;
  unknown groups collapse into an "Other" bucket.
- **Search**: filters by name, description, or id (debounced, case-insensitive).
- **Sort**: default (definition order), unlocked-first, by rarity
  (common→legendary), or newest-first (by `unlocked_at`).
- **Stat strip**: total unlocked / 166, per-rarity counts, total turns, total
  tool calls, longest streak.
- **Recently unlocked** ticker (marquee) fed by `newly_unlocked`.
- **Tile states**: unlocked = emoji in a rarity-colored ring with glow;
  in-progress = rarity ring + `current/target` progress bar with %; locked =
  greyscale silhouette.
- **Hover** reveals the precise unlock timestamp; rarity chip + ✓ UNLOCKED
  stamp on each tile.
- **Completionist**: golden banner when the `completionist` achievement (or
  100%) is reached.

## Rarity colors

| Rarity     | Color    |
|------------|----------|
| common     | `#9CA3AF` |
| uncommon   | `#22C55E` |
| rare       | `#3B82F6` |
| epic       | `#A855F7` |
| legendary  | `#F59E0B` |

## Regenerating defs.js

```bash
cd /root/hermes-achievements-plugin
python3 - <<'PY'
import re, ast, json
src = open('__init__.py').read()
lines = src.splitlines()
start = next(i for i,l in enumerate(lines) if 'ACHIEVEMENT_DEFS = {' in l)
depth = 0; end = start
for j in range(start, len(lines)):
    for ch in lines[j]:
        depth += (ch=='{') - (ch=='}')
    if depth == 0:
        end = j; break
body = "\n".join(lines[start:end+1]).split('=',1)[1].strip().rstrip(',')
d = ast.literal_eval(body)
open('webui/defs.js','w').write(
    "// Generated from ACHIEVEMENT_DEFS.\nwindow.ACHIEVEMENT_DEFS = "
    + json.dumps(d, ensure_ascii=False, indent=2) + ";\n")
print("wrote", len(d), "defs")
PY
```

## Repo boundary

This directory is purely additive. Nothing under `webui/` touches the
plugin's `__init__.py`, tests, `plugin.yaml`, or Python logic. Drop the whole
`webui/` folder anywhere and serve it.