#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Hermes Achievements Plugin — Install Script
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   ./setup.sh              Interactive install
#   ./setup.sh --ci         Unattended install (no prompts)
#   ./setup.sh --test       Run the test suite after install
#   ./setup.sh --help       Show this help
#
# Installs the plugin to ~/.hermes/plugins/achievements/, validates the
# environment, enables the plugin, and offers to restart the gateway.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

CI_MODE=false
RUN_TESTS=false
for arg in "$@"; do
    case "$arg" in
        --ci) CI_MODE=true ;;
        --test) RUN_TESTS=true ;;
        --help|-h)
            sed -n '/^# Usage:/,/^set -e/p' "$0" | head -n -1
            exit 0
            ;;
    esac
done

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERR]${NC}   $*"; }
step()  { echo -e "\n${CYAN}── $* ──${NC}"; }

# ── Detect paths ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/achievements"
ENV_FILE="$HERMES_HOME/.env"

echo -e "${CYAN}"
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║   Hermes Achievements Plugin — Setup          ║"
echo "  ║   v2.14.0  •  144 achievements  •  4 languages ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: Prerequisites ───────────────────────────────────────────────────
step "1/4  Checking prerequisites"

# Check Python
PYTHON=$(command -v python3 || command -v python || true)
if [[ -z "$PYTHON" ]]; then
    err "Python 3 is required but not found."
    err "Install it: https://www.python.org/downloads/"
    exit 1
fi
PYVER=$("$PYTHON" --version 2>&1 | grep -oP '\d+\.\d+')
if [[ "$(echo "$PYVER" | cut -d. -f1)" -lt 3 ]] || { [[ "$(echo "$PYVER" | cut -d. -f1)" -eq 3 ]] && [[ "$(echo "$PYVER" | cut -d. -f2)" -lt 11 ]]; }; then
    err "Python 3.11+ required (found $PYVER)"
    exit 1
fi
ok "Python $PYVER found at $PYTHON"

# Check Hermes
if ! command -v hermes &>/dev/null; then
    warn "Hermes CLI not found in PATH. Plugin files will be installed but"
    warn "you'll need to enable the plugin manually after confirming Hermes is installed."
    HERMES_CMD=""
else
    HERMES_CMD="hermes"
    ok "Hermes CLI found"
fi

# Check Hermes home
if [[ ! -d "$HERMES_HOME/plugins" ]]; then
    mkdir -p "$HERMES_HOME/plugins"
    warn "Created $HERMES_HOME/plugins/"
fi
ok "Hermes home: $HERMES_HOME"

# ── Step 2: Install files ───────────────────────────────────────────────────
step "2/4  Installing plugin files"

mkdir -p "$PLUGIN_DIR"

# Copy all files, preserving locales/scripts/tests
rsync -a --delete \
    --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \
    "$SCRIPT_DIR/" "$PLUGIN_DIR/" 2>/dev/null || \
cp -r "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/*.yaml "$SCRIPT_DIR"/*.toml \
      "$SCRIPT_DIR"/*.md "$SCRIPT_DIR"/LICENSE \
      "$SCRIPT_DIR"/locales/ "$SCRIPT_DIR"/scripts/ "$SCRIPT_DIR"/tests/ \
      "$PLUGIN_DIR/" 2>/dev/null

# Verify critical files
for f in __init__.py plugin.yaml locales/en.json; do
    if [[ ! -f "$PLUGIN_DIR/$f" ]]; then
        err "Failed to install $f"
        exit 1
    fi
done
ok "Files installed to $PLUGIN_DIR/"

# Run the test suite if requested
if $RUN_TESTS; then
    step "2b/4  Running test suite"
    if (cd "$PLUGIN_DIR" && python3 -m pytest tests/ -q); then
        ok "All tests passed"
    else
        err "Test suite failed — the installed plugin may be incomplete."
        exit 1
    fi
    if (cd "$PLUGIN_DIR" && python3 scripts/check_plugin.py); then
        ok "Health check passed (defs, locales, manifest↔register agreement)"
    else
        err "Health check failed — installed files are inconsistent."
        exit 1
    fi
    if (cd "$PLUGIN_DIR" && python3 scripts/check_plugin.py --gateway); then
        ok "Gateway kwarg contract check passed (or skipped: no Hermes source)"
    else
        err "Gateway kwarg contract check failed — a hook kwarg the plugin "
            "reads is no longer delivered by the installed Hermes."
        exit 1
    fi
fi

# ── Step 3: Validate environment ────────────────────────────────────────────
step "3/4  Validating environment"

# Check Discord token
TOKEN_OK=false
if [[ -f "$ENV_FILE" ]]; then
    if grep -q "^DISCORD_BOT_TOKEN=" "$ENV_FILE" 2>/dev/null; then
        TOKEN_VAL=$(grep "^DISCORD_BOT_TOKEN=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d "'\"")
        if [[ -n "$TOKEN_VAL" ]]; then
            TOKEN_OK=true
            ok "DISCORD_BOT_TOKEN found in $ENV_FILE"
        fi
    fi
fi
if ! $TOKEN_OK; then
    warn "DISCORD_BOT_TOKEN not found in $ENV_FILE"
    warn "Achievement notifications will not be delivered without it."
    warn "Add it: echo 'DISCORD_BOT_TOKEN=your_token' >> $ENV_FILE"

    if ! $CI_MODE; then
        read -rp "  Add it now? (y/N): " ADD_TOKEN
        if [[ "$ADD_TOKEN" =~ ^[Yy] ]]; then
            read -rp "  Enter bot token: " NEW_TOKEN
            if [[ -n "$NEW_TOKEN" ]]; then
                echo "DISCORD_BOT_TOKEN=$NEW_TOKEN" >> "$ENV_FILE"
                ok "Token added to $ENV_FILE"
            fi
        fi
    fi
fi

# Check home channel
if [[ -f "$ENV_FILE" ]]; then
    if ! grep -q "^DISCORD_HOME_CHANNEL=" "$ENV_FILE" 2>/dev/null; then
        warn "DISCORD_HOME_CHANNEL not set in $ENV_FILE"
        warn "Notifications won't know where to deliver. Add it:"
        warn "  echo 'DISCORD_HOME_CHANNEL=your_channel_id' >> $ENV_FILE"
    else
        ok "DISCORD_HOME_CHANNEL configured"
    fi
fi

# ── Step 4: Enable plugin ───────────────────────────────────────────────────
step "4/4  Enabling plugin"

if [[ -n "$HERMES_CMD" ]]; then
    # Check if already enabled
    STATUS=$("$HERMES_CMD" plugins list 2>/dev/null | grep achievements || true)
    if echo "$STATUS" | grep -qi "enabled"; then
        ok "Plugin already enabled"
    else
        if $CI_MODE; then
            "$HERMES_CMD" plugins enable achievements 2>/dev/null && ok "Plugin enabled"
        else
            echo -n "  Enable plugin now? (Y/n): "
            read -r ENABLE
            if [[ -z "$ENABLE" || "$ENABLE" =~ ^[Yy] ]]; then
                "$HERMES_CMD" plugins enable achievements 2>/dev/null && ok "Plugin enabled" || warn "Could not enable plugin"
            fi
        fi
    fi

    # Offer gateway restart
    if ! $CI_MODE; then
        echo ""
        echo -n "  Restart Hermes gateway to load the plugin? (y/N): "
        read -r RESTART
        if [[ "$RESTART" =~ ^[Yy] ]]; then
            info "Restarting gateway..."
            "$HERMES_CMD" gateway restart 2>/dev/null && ok "Gateway restarted" || warn "Could not restart gateway (try manually: hermes gateway restart)"
        fi
    fi
else
    warn "Hermes CLI not found — plugin files installed but not enabled."
    warn "Install Hermes first, then run: hermes plugins enable achievements"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
step "✅ Setup complete!"

echo "  Plugin:   $PLUGIN_DIR/"
echo "  Version:  v2.14.0"
echo "  Locales:  en  es  fr  pt"
echo ""
echo "  Usage:"
echo "    /achievements              View all"
echo "    /achievements stats        Stats"
echo "    /achievements lang es      Switch language"
echo "    /achievement first_steps   Detail"
echo ""
echo "  Repo: https://github.com/omiinaya/hermes-achievements-plugin"
echo ""
