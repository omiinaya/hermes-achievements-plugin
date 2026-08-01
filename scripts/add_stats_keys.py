#!/usr/bin/env python3
"""Add stats UI keys for the new v2.3.0 counters to all 4 locales."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NEW_KEYS = {
    "en": {
        "stats_approvals_always": "Permanent approvals:     {count}",
        "stats_approvals_denied": "Approvals denied:         {count}",
        "stats_session_resets": "Session resets:           {count}",
    },
    "es": {
        "stats_approvals_always": "Aprobaciones permanentes: {count}",
        "stats_approvals_denied": "Aprobaciones rechazadas:  {count}",
        "stats_session_resets": "Reinicios de sesión:      {count}",
    },
    "fr": {
        "stats_approvals_always": "Approbations permanentes: {count}",
        "stats_approvals_denied": "Approbations refusées:    {count}",
        "stats_session_resets": "Réinitialisations:        {count}",
    },
    "pt": {
        "stats_approvals_always": "Aprovações permanentes:   {count}",
        "stats_approvals_denied": "Aprovações negadas:       {count}",
        "stats_session_resets": "Reinicializações:         {count}",
    },
}

for code in ("en", "es", "fr", "pt"):
    path = os.path.join(ROOT, "locales", f"{code}.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("ui", {}).update(NEW_KEYS[code])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{code}: ui keys = {len(data['ui'])}")
