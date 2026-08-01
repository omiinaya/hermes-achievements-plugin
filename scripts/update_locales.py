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
    "show_and_tell": {
        "es": {"name": "Muestra y Cuenta", "description": "Envía una imagen o adjunto multimedia a Hermes"},
        "fr": {"name": "Montre et Raconte", "description": "Envoyez une image ou une pièce jointe à Hermes"},
        "pt": {"name": "Mostre e Conte", "description": "Envie uma imagem ou anexo de mídia ao Hermes"},
    },
    "visual_storyteller": {
        "es": {"name": "Narrador Visual", "description": "Envía 25 imágenes o adjuntos multimedia"},
        "fr": {"name": "Conteur Visuel", "description": "Envoyez 25 images ou pièces jointes"},
        "pt": {"name": "Contador Visual", "description": "Envie 25 imagens ou anexos de mídia"},
    },
    "deep_context": {
        "es": {"name": "Contexto Profundo", "description": "Haz una solicitud de API con 50+ mensajes en contexto"},
        "fr": {"name": "Contexte Profond", "description": "Faites une requête API avec 50+ messages en contexte"},
        "pt": {"name": "Contexto Profundo", "description": "Faça uma solicitação de API com 50+ mensagens no contexto"},
    },
    "context_colossus": {
        "es": {"name": "Contexto Colosal", "description": "Haz una solicitud de API con 100+ mensajes en contexto"},
        "fr": {"name": "Contexte Colossal", "description": "Faites une requête API avec 100+ messages en contexte"},
        "pt": {"name": "Contexto Colossal", "description": "Faça uma solicitação de API com 100+ mensagens no contexto"},
    },
    "wordsmith": {
        "es": {"name": "Orfebre de Palabras", "description": "Envía un único mensaje de 300+ palabras"},
        "fr": {"name": "Forgeron de Mots", "description": "Envoyez un seul message de 300+ mots"},
        "pt": {"name": "Artesão de Palavras", "description": "Envie uma única mensagem com 300+ palavras"},
    },
    "novelist": {
        "es": {"name": "Novelista", "description": "Envía un único mensaje de 1500+ palabras"},
        "fr": {"name": "Romancier", "description": "Envoyez un seul message de 1500+ mots"},
        "pt": {"name": "Romancista", "description": "Envie uma única mensagem com 1500+ palavras"},
    },
    "local_first": {
        "es": {"name": "Local Primero", "description": "Ejecuta Hermes contra un endpoint de modelo local"},
        "fr": {"name": "Local d'Abord", "description": "Exécutez Hermes sur un endpoint de modèle local"},
        "pt": {"name": "Local Primeiro", "description": "Execute o Hermes contra um endpoint de modelo local"},
    },
    "self_hosted": {
        "es": {"name": "Autoalojado", "description": "Haz 25 solicitudes de API a endpoints locales"},
        "fr": {"name": "Auto-Hébergé", "description": "Faites 25 requêtes API vers des endpoints locaux"},
        "pt": {"name": "Auto-Hospedado", "description": "Faça 25 solicitações de API para endpoints locais"},
    },
    "context_monster": {
        "es": {"name": "Monstruo de Contexto", "description": "Envía una solicitud de API con 200K+ tokens de entrada"},
        "fr": {"name": "Monstre de Contexte", "description": "Envoyez une requête API avec 200K+ tokens d'entrée"},
        "pt": {"name": "Monstro de Contexto", "description": "Envie uma solicitação de API com 200K+ tokens de entrada"},
    },
    "token_tsunami": {
        "es": {"name": "Tsunami de Tokens", "description": "Envía una solicitud de API con 500K+ tokens de entrada"},
        "fr": {"name": "Tsunami de Tokens", "description": "Envoyez une requête API avec 500K+ tokens d'entrée"},
        "pt": {"name": "Tsunami de Tokens", "description": "Envie uma solicitação de API com 500K+ tokens de entrada"},
    },
    "icebreaker": {
        "es": {"name": "Rompehielos", "description": "Inicia tu primera conversación"},
        "fr": {"name": "Brise-Glace", "description": "Démarrez votre première conversation"},
        "pt": {"name": "Quebra-Gelo", "description": "Inicie sua primeira conversa"},
    },
    "conversation_habit": {
        "es": {"name": "Hábito de Conversación", "description": "Inicia 10 conversaciones"},
        "fr": {"name": "Habitude de Conversation", "description": "Démarrez 10 conversations"},
        "pt": {"name": "Hábito de Conversa", "description": "Inicie 10 conversas"},
    },
    "serial_starter": {
        "es": {"name": "Iniciador en Serie", "description": "Inicia 50 conversaciones"},
        "fr": {"name": "Serial Démarreur", "description": "Démarrez 50 conversations"},
        "pt": {"name": "Iniciador em Série", "description": "Inicie 50 conversas"},
    },
    "conversation_colossus": {
        "es": {"name": "Coloso de la Conversación", "description": "Inicia 100 conversaciones"},
        "fr": {"name": "Colosse de la Conversation", "description": "Démarrez 100 conversations"},
        "pt": {"name": "Colosso da Conversa", "description": "Inicie 100 conversas"},
    },
    "double_time": {
        "es": {"name": "Doble Turno", "description": "Emite 2 llamadas de herramienta en una sola respuesta"},
        "fr": {"name": "Double Temps", "description": "Émettez 2 appels d'outil en une seule réponse"},
        "pt": {"name": "Dupla Jornada", "description": "Emita 2 chamadas de ferramenta em uma única resposta"},
    },
    "batch_artist": {
        "es": {"name": "Artista de Lotes", "description": "Emite 5 llamadas de herramienta en una sola respuesta"},
        "fr": {"name": "Artiste du Lot", "description": "Émettez 5 appels d'outil en une seule réponse"},
        "pt": {"name": "Artista de Lotes", "description": "Emita 5 chamadas de ferramenta em uma única resposta"},
    },
    "parallel_barrage": {
        "es": {"name": "Ráfaga Paralela", "description": "Emite 10 llamadas de herramienta en una sola respuesta"},
        "fr": {"name": "Rafale Parallèle", "description": "Émettez 10 appels d'outil en une seule réponse"},
        "pt": {"name": "Rajada Paralela", "description": "Emita 10 chamadas de ferramenta em uma única resposta"},
    },
    "tool_torrent": {
        "es": {"name": "Torrente de Herramientas", "description": "Emite 20 llamadas de herramienta en una sola respuesta"},
        "fr": {"name": "Torrent d'Outils", "description": "Émettez 20 appels d'outil en une seule réponse"},
        "pt": {"name": "Torrente de Ferramentas", "description": "Emita 20 chamadas de ferramenta em uma única resposta"},
    },
    "verbose_output": {
        "es": {"name": "Salida Verbosa", "description": "Produce 100KB+ de salida de un solo comando de terminal"},
        "fr": {"name": "Sortie Verbouse", "description": "Produisez 100KB+ de sortie d'une seule commande terminale"},
        "pt": {"name": "Saída Verbosa", "description": "Produza 100KB+ de saída de um único comando de terminal"},
    },
    "data_flood": {
        "es": {"name": "Inundación de Datos", "description": "Produce 1MB+ de salida de un solo comando de terminal"},
        "fr": {"name": "Déluge de Données", "description": "Produisez 1MB+ de sortie d'une seule commande terminale"},
        "pt": {"name": "Enchente de Dados", "description": "Produza 1MB+ de saída de um único comando de terminal"},
    },
    "multi_env": {
        "es": {"name": "Multi-Entorno", "description": "Ejecuta comandos de terminal en 2 entornos de ejecución diferentes"},
        "fr": {"name": "Multi-Environnement", "description": "Exécutez des commandes terminales dans 2 environnements d'exécution différents"},
        "pt": {"name": "Multi-Ambiente", "description": "Execute comandos de terminal em 2 ambientes de execução diferentes"},
    },
    "omnipresent": {
        "es": {"name": "Omnipresente", "description": "Ejecuta comandos de terminal en 5 entornos de ejecución diferentes"},
        "fr": {"name": "Omniprésent", "description": "Exécutez des commandes terminales dans 5 environnements d'exécution différents"},
        "pt": {"name": "Onipresente", "description": "Execute comandos de terminal em 5 ambientes de execução diferentes"},
    },
    "ghost_command": {
        "es": {"name": "Comando Fantasma", "description": "Consigue el código de salida 127 (comando no encontrado) en un comando de terminal"},
        "fr": {"name": "Commande Fantôme", "description": "Obtenez le code de sortie 127 (commande introuvable) sur une commande terminale"},
        "pt": {"name": "Comando Fantasma", "description": "Obtenha o código de saída 127 (comando não encontrado) em um comando de terminal"},
    },
    "big_haul": {
        "es": {"name": "Gran Botín", "description": "Recibe un resultado de 1MB+ de una sola llamada de herramienta"},
        "fr": {"name": "Grosse Prise", "description": "Recevez un résultat de 1MB+ d'un seul appel d'outil"},
        "pt": {"name": "Grande Pegada", "description": "Receba um resultado de 1MB+ de uma única chamada de ferramenta"},
    },
    "colossal_result": {
        "es": {"name": "Resultado Colosal", "description": "Recibe un resultado de 10MB+ de una sola llamada de herramienta"},
        "fr": {"name": "Résultat Colossal", "description": "Recevez un résultat de 10MB+ d'un seul appel d'outil"},
        "pt": {"name": "Resultado Colossal", "description": "Receba um resultado de 10MB+ de uma única chamada de ferramenta"},
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
