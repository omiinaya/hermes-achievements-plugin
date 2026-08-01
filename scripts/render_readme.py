#!/usr/bin/env python3
"""Regenerate the derived sections of README.md from ACHIEVEMENT_DEFS.

The README's achievement tables AND the example-output block are generated
from the source of truth (ACHIEVEMENT_DEFS in __init__.py + group/emoji
maps + recognition thresholds) so they can never drift again. Static
sections (intro, quick start, architecture, dev) are preserved verbatim.

Usage:  python3 scripts/render_readme.py
"""
import ast
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "__init__.py")
README = os.path.join(ROOT, "README.md")

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary"]
RARITY_LABEL = {
    "common": "Common", "uncommon": "Uncommon", "rare": "Rare",
    "epic": "Epic", "legendary": "Legendary",
}

# Recognition maps to literal-eval for threshold lookups (aid → min
# threshold). Kept in sync with the constant names in __init__.py.
_THRESHOLD_CONSTANTS = [
    "_TOOL_THRESHOLDS",
    "_TOTAL_TOOL_THRESHOLDS",
    "_MESSAGE_THRESHOLDS",
    "_COUNTER_THRESHOLDS",
]

# Illustrative progress for the example block's hypothetical user. These
# numerators are the ONLY hand-picked numbers — denominators, bars and
# percents are derived from the source of truth so they can never rot
# again (they did: 10→16, 23→44, 18→40, 15→19 and Deep Diver 2/5 when
# the def moved to 25 web searches).
EXAMPLE_GROUP_PROGRESS = {
    "Getting Started": 2, "Tools & Skills": 2, "Power User": 0,
    "Expert": 0, "Milestones": 0, "Community": 0,
}
EXAMPLE_NEXT_UP = [("Deep Diver", 5), ("Config Guru", 2), ("Terminal Jockey", 3)]


def load_constants():
    """Literal-eval every top-level constant the README derives from."""
    with open(PLUGIN, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    constants = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                constants[target.id] = ast.literal_eval(node.value)
            except Exception:  # noqa: BLE001, S112 — skip non-literal assigns
                continue
    return constants


def _progress_bar(current, target, width=10):
    """Mirror __init__._progress_bar (int truncation, not round)."""
    if target <= 0:
        return "░" * width
    filled = min(int(current / target * width), width)
    return "█" * filled + "░" * (width - filled)


def render_groups(defs, groups, group_emojis):
    out = []
    for group in groups:
        items = sorted(
            ((d["name"], d["emoji"], d["description"], d["rarity"], aid) for aid, d in defs.items() if d["group"] == group),
            key=lambda x: RARITY_ORDER.index(x[3]) if x[3] in RARITY_ORDER else 99,
        )
        emoji = group_emojis.get(group, "🎮")
        out.append(f"### {emoji} {group} ({len(items)})")
        out.append("")
        out.append("| Icon | Name | Description | Rarity |")
        out.append("|------|------|-------------|--------|")
        for name, icon, desc, rarity, _ in items:
            out.append(f"| {icon} | {name} | {desc} | {RARITY_LABEL.get(rarity, rarity.title())} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_example_block(readme, defs, groups, constants):
    """Own every derived number inside the '### Example output' block.

    Rewrites (with loud failure if a line is missing):
      - group summary lines:  **{group}** ({num}/{size}) {bar}
      - next-up lines:        {rarity} **{name}** — {bar} {cur}/{tgt} ({pct}%)
      - closest-to-unlock:    🔮 Closest to unlock: **{name}** {bar} {cur}/{tgt} ({pct}%)
    """
    name_to_aid = {d["name"]: aid for aid, d in defs.items()}
    thresholds = {}
    for const in _THRESHOLD_CONSTANTS:
        value = constants.get(const) or {}
        entries = value.items() if isinstance(value, dict) else [(None, value)]
        for _stat, tiers in entries:
            for threshold, aid in tiers:
                if aid not in thresholds or threshold < thresholds[aid]:
                    thresholds[aid] = threshold
    rarity_emojis = constants.get("RARITY_EMOJIS", {})
    group_emojis = constants.get("GROUP_EMOJIS", {})

    def _rewrite(pattern, replacement, what):
        nonlocal readme
        readme, n = pattern.subn(replacement, readme)
        if n != 1:
            raise SystemExit(f"ERROR: example block must render {what} exactly once (found {n})")
        return readme

    # Group summary lines (denominator + bar derive from GROUPS; the
    # numerator is the illustrative EXAMPLE_GROUP_PROGRESS value).
    for group in groups:
        size = sum(1 for d in defs.values() if d["group"] == group)
        num = EXAMPLE_GROUP_PROGRESS.get(group)
        if num is None:
            raise SystemExit(f"ERROR: EXAMPLE_GROUP_PROGRESS missing group '{group}'")
        line = f"{group_emojis.get(group, '')} **{group}** ({num}/{size}) {_progress_bar(num, size)}"
        _rewrite(
            re.compile(rf"^{re.escape(group_emojis.get(group, ''))} \*\*{re.escape(group)}\*\* \(\d+/\d+\) [█░]+$", re.MULTILINE),
            line, f"group line for '{group}'",
        )

    # Next-up list + closest-to-unlock hint (bar/denominator/pct derive
    # from the recognition thresholds; color from the def's rarity).
    for name, cur in EXAMPLE_NEXT_UP:
        aid = name_to_aid.get(name)
        if aid is None:
            raise SystemExit(f"ERROR: example next-up references unknown achievement '{name}'")
        tgt = thresholds.get(aid)
        if tgt is None:
            raise SystemExit(
                f"ERROR: no recognition threshold found for '{name}' ({aid}) — "
                "the renderer cannot derive its progress line")
        rarity_e = rarity_emojis.get(defs[aid].get("rarity", "common"), "⬜")
        bar = _progress_bar(cur, tgt)
        pct = int(cur / tgt * 100)
        _rewrite(
            re.compile(rf"^[⬜🟩🟦🟣🟡] \*\*{re.escape(name)}\*\* — [█░]+ \d+/\d+ \(\d+%\)$", re.MULTILINE),
            f"{rarity_e} **{name}** — {bar} {cur}/{tgt} ({pct}%)",
            f"next-up line for '{name}'",
        )
        if name == EXAMPLE_NEXT_UP[0][0]:
            _rewrite(
                re.compile(rf"^🔮 Closest to unlock: \*\*{re.escape(name)}\*\* [█░]+ \d+/\d+ \(\d+%\)$", re.MULTILINE),
                f"🔮 Closest to unlock: **{name}** {bar} {cur}/{tgt} ({pct}%)",
                f"closest-to-unlock hint for '{name}'",
            )
    return readme


def main():
    constants = load_constants()
    defs = constants.get("ACHIEVEMENT_DEFS", {})
    groups = list(constants.get("GROUPS", []))
    group_emojis = constants.get("GROUP_EMOJIS", {})
    # NOTE: the exact def count is enforced by tests/test_plugin.py — this
    # script renders whatever ACHIEVEMENT_DEFS contains (source of truth).

    with open(README, encoding="utf-8") as f:
        readme = f.read()

    # Keep the header badge count in sync with ACHIEVEMENT_DEFS. The
    # leading "**N Steam-style achievement badges**" line is hardcoded in
    # the template — rewrite the number so it can never drift stale again.
    readme = re.sub(
        r"\*\*\d+ Steam-style achievement badges\*\*",
        f"**{len(defs)} Steam-style achievement badges**",
        readme,
    )
    readme = re.sub(
        r"# simulation that proves all \d+ achievements can unlock",
        f"# simulation that proves all {len(defs)} achievements can unlock",
        readme,
    )

    marker_start = "## Achievement Groups"
    marker_end = "## Architecture"
    start = readme.index(marker_start)
    end = readme.index(marker_end)
    new_section = f"{marker_start}\n\n" + render_groups(defs, groups, group_emojis) + "\n"
    readme = readme[:start] + new_section + readme[end:]

    readme = render_example_block(readme, defs, groups, constants)

    with open(README, "w", encoding="utf-8") as f:
        f.write(readme)

    total = sum(len([d for d in defs.values() if d["group"] == g]) for g in groups)
    print(f"OK: {len(defs)} achievements, {len(groups)} groups, {total} total in tables")


if __name__ == "__main__":
    main()
