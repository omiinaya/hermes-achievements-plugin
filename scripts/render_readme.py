#!/usr/bin/env python3
"""Regenerate the achievement group tables in README.md from ACHIEVEMENT_DEFS.

The README's achievement tables are generated from the source of truth
(ACHIEVEMENT_DEFS in __init__.py + group/emoji maps) so they can never
drift again. Static sections (intro, quick start, architecture, dev) are
preserved verbatim.

Usage:  python3 scripts/render_readme.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "__init__.py")
README = os.path.join(ROOT, "README.md")

RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary"]
RARITY_LABEL = {
    "common": "Common", "uncommon": "Uncommon", "rare": "Rare",
    "epic": "Epic", "legendary": "Legendary",
}


def load_defs():
    with open(PLUGIN, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    defs = {}
    groups = []
    group_emojis = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                value = ast.literal_eval(node.value)
            except Exception:  # noqa: BLE001, S112 — skip non-literal assigns
                continue
            if target.id == "ACHIEVEMENT_DEFS":
                defs = value
            elif target.id == "GROUPS":
                groups = list(value)
            elif target.id == "GROUP_EMOJIS":
                group_emojis = value
    return defs, groups, group_emojis


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


def main():
    defs, groups, group_emojis = load_defs()
    if len(defs) != 107:
        print(f"WARNING: {len(defs)} achievements (expected 107)", file=sys.stderr)

    with open(README, encoding="utf-8") as f:
        readme = f.read()

    marker_start = "## Achievement Groups"
    marker_end = "## Architecture"
    start = readme.index(marker_start)
    end = readme.index(marker_end)
    new_section = f"{marker_start}\n\n" + render_groups(defs, groups, group_emojis) + "\n"
    readme = readme[:start] + new_section + readme[end:]

    with open(README, "w", encoding="utf-8") as f:
        f.write(readme)

    total = sum(len([d for d in defs.values() if d["group"] == g]) for g in groups)
    print(f"OK: {len(defs)} achievements, {len(groups)} groups, {total} total in tables")


if __name__ == "__main__":
    main()
