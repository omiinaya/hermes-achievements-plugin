#!/usr/bin/env python3
"""Update locale files: remove 5 dead achievements, add 5 new ones.

Keeps the achievement dict alphabetically sorted, matching the existing
locale file convention.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REMOVE = ["star_gazer", "updater", "theme_setter", "feedback_friend", "helpful_soul"]

NEW = {
    "trust_fall": {
        "en": {"name": "Trust Fall", "description": "Approve a command permanently with 'always'"},
        "es": {"name": "Caída de Confianza", "description": "Aprueba un comando permanentemente con 'always'"},
        "fr": {"name": "Chute de Confiance", "description": "Approuvez une commande définitivement avec « always »"},
        "pt": {"name": "Queda de Confiança", "description": "Aprove um comando permanentemente com 'always'"},
    },
    "cautious": {
        "en": {"name": "Cautious", "description": "Deny an approval request"},
        "es": {"name": "Cauteloso", "description": "Rechaza una solicitud de aprobación"},
        "fr": {"name": "Prudent", "description": "Refusez une demande d'approbation"},
        "pt": {"name": "Cauteloso", "description": "Negue uma solicitação de aprovação"},
    },
    "orchestrator": {
        "en": {"name": "Orchestrator", "description": "Use an orchestrator-role subagent"},
        "es": {"name": "Orquestador", "description": "Usa un subagente con rol de orquestador"},
        "fr": {"name": "Orchestrateur", "description": "Utilisez un sous-agent avec le rôle d'orchestrateur"},
        "pt": {"name": "Orquestrador", "description": "Use um subagente com função de orquestrador"},
    },
    "resilient": {
        "en": {"name": "Resilient", "description": "Complete a task after a subagent failed"},
        "es": {"name": "Resiliente", "description": "Completa una tarea después de que un subagente falle"},
        "fr": {"name": "Résilient", "description": "Terminez une tâche après l'échec d'un sous-agent"},
        "pt": {"name": "Resiliente", "description": "Conclua uma tarefa após uma falha de subagente"},
    },
    "fresh_start": {
        "en": {"name": "Fresh Start", "description": "Start a fresh session with /new or /reset"},
        "es": {"name": "Nuevo Comienzo", "description": "Inicia una sesión nueva con /new o /reset"},
        "fr": {"name": "Nouveau Départ", "description": "Démarrez une nouvelle session avec /new ou /reset"},
        "pt": {"name": "Novo Começo", "description": "Inicie uma nova sessão com /new ou /reset"},
    },
}

for code in ("en", "es", "fr", "pt"):
    path = os.path.join(ROOT, "locales", f"{code}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    ach = data.get("achievement", {})
    for key in REMOVE:
        ach.pop(key, None)
    for key, entry in NEW.items():
        ach[key] = entry[code]

    # Alphabetical sort to match existing convention
    data["achievement"] = dict(sorted(ach.items()))

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{code}: {len(ach)} achievement entries")
