#!/usr/bin/env python3
"""Update locale files: remove dead achievements, add new ones.

Keeps the achievement dict alphabetically sorted, matching the existing
locale file convention. English name/description is pulled from
ACHIEVEMENT_DEFS in __init__.py so the source of truth is always the defs.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "__init__.py")


def load_defs():
    """Extract ACHIEVEMENT_DEFS from __init__.py without importing it."""
    with open(INIT, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"ACHIEVEMENT_DEFS\s*=\s*\{", src)
    if not m:
        raise SystemExit("ACHIEVEMENT_DEFS not found")
    seg = src[m.start():]
    depth = 0
    end = -1
    for i, ch in enumerate(seg):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise SystemExit("ACHIEVEMENT_DEFS block unterminated")
    ns = {}
    exec(seg[: end + 1], ns)  # noqa: S102 — local script, trusted source
    return ns["ACHIEVEMENT_DEFS"]


# Removed achievements (no longer in ACHIEVEMENT_DEFS) — always pruned
# automatically. This list is kept for documentation only.
REMOVED = ["version_spotter", "plugin_browser", "skill_browser"]

# Manual translations for NEW achievements. English is auto-synced from
# ACHIEVEMENT_DEFS, so only es/fr/pt need entries here.
NEW = {
    "conductor": {
        "es": {"name": "Director de Orquesta", "description": "Ejecuta 3 subagentes a la vez (pico de concurrencia)"},
        "fr": {"name": "Chef d'Orchestre", "description": "Exécutez 3 sous-agents en même temps (concurrence maximale)"},
        "pt": {"name": "Maestro de Orquestra", "description": "Execute 3 subagentes ao mesmo tempo (pico de concorrência)"},
    },
    "indestructible": {
        "es": {"name": "Indestructible", "description": "Sobrevive a 10 errores de API del LLM sin rendirte"},
        "fr": {"name": "Indestructible", "description": "Survivez à 10 erreurs d'API LLM sans abandonner"},
        "pt": {"name": "Indestrutível", "description": "Sobreviva a 10 erros de API do LLM sem desistir"},
    },
    "under_scrutiny": {
        "es": {"name": "Bajo Escrutinio", "description": "Provoca 10 solicitudes de aprobación"},
        "fr": {"name": "Sous Surveillance", "description": "Déclenchez 10 demandes d'approbation"},
        "pt": {"name": "Sob Escrutínio", "description": "Dispare 10 solicitações de aprovação"},
    },
    "social_butterfly": {
        "es": {"name": "Mariposa Social", "description": "Recibe mensajes de 3 usuarios diferentes"},
        "fr": {"name": "Papillon Social", "description": "Recevez des messages de 3 utilisateurs différents"},
        "pt": {"name": "Borboleta Social", "description": "Receba mensagens de 3 usuários diferentes"},
    },
    "party_host": {
        "es": {"name": "Anfitrión de Fiesta", "description": "Recibe mensajes de 10 usuarios diferentes"},
        "fr": {"name": "Hôte de Fête", "description": "Recevez des messages de 10 utilisateurs différents"},
        "pt": {"name": "Anfitrião de Festa", "description": "Receba mensagens de 10 usuários diferentes"},
    },
}


def main():
    defs = load_defs()
    for code in ("en", "es", "fr", "pt"):
        path = os.path.join(ROOT, "locales", f"{code}.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ach = data.get("achievement", {})

        # Prune achievements that no longer exist
        for key in list(ach):
            if key not in defs:
                ach.pop(key, None)

        # Add any missing achievements (English from defs, others from NEW).
        # NEW translations are authoritative — overwrite stale English
        # fallbacks left by a previous run.
        for key, adef in defs.items():
            if code == "en":
                if key not in ach:
                    ach[key] = {"name": adef["name"], "description": adef["description"]}
            elif key in NEW:
                if key not in ach or ach[key] != NEW[key][code]:
                    ach[key] = NEW[key][code]
            elif key not in ach:
                print(f"WARN: no {code} translation for '{key}' — falling back to English")
                ach[key] = {"name": adef["name"], "description": adef["description"]}

        data["achievement"] = dict(sorted(ach.items()))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"{code}: {len(ach)} achievement entries")


if __name__ == "__main__":
    sys.exit(main())
