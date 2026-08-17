#!/usr/bin/env bash
# install-everything.sh — first-time install of cc-session-tools.
#
# Installs the CLIs, bundled skills, hooks, and the ccl shell function in one
# shot. Idempotent: safe to re-run after an upgrade.
#
# Usage:
#   # Install from PyPI (recommended)
#   bash install-everything.sh
#
#   # Install / reinstall from a local clone
#   bash install-everything.sh --from-source
#
#   # Upgrade in place (same as install for uv)
#   bash install-everything.sh --upgrade
#
# After a successful run, restart your shell (or source ~/.bashrc / ~/.zshrc)
# to pick up the ccl() function.
#
# For FUTURE UPGRADES (once the CLIs are already installed), you only need:
#   uv tool install cc-session-tools --upgrade
# ccst syncs its own config on the next command you run.

set -euo pipefail

FROM_SOURCE=0
UPGRADE=0

for arg in "$@"; do
    case "$arg" in
        --from-source) FROM_SOURCE=1 ;;
        --upgrade)     UPGRADE=1 ;;
        --help|-h)
            sed -n '2,20p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { echo ""; echo "=== $* ==="; }

# ── Step 1: Install / upgrade CLIs ──────────────────────────────────────────
step "1/3  CLIs"
if command -v uv >/dev/null 2>&1; then
    if [[ $FROM_SOURCE -eq 1 ]]; then
        echo "Installing from source: $REPO_DIR"
        uv tool install --reinstall "$REPO_DIR"
    else
        uv tool install cc-session-tools
    fi
elif command -v pipx >/dev/null 2>&1; then
    if [[ $FROM_SOURCE -eq 1 ]]; then
        pipx install --force "$REPO_DIR"
    elif [[ $UPGRADE -eq 1 ]]; then
        pipx upgrade cc-session-tools
    else
        pipx install cc-session-tools
    fi
else
    echo "ERROR: neither uv nor pipx found. Install one first:" >&2
    echo "  uv:   https://docs.astral.sh/uv/getting-started/installation/" >&2
    echo "  pipx: https://pipx.pypa.io/stable/installation/" >&2
    exit 1
fi

# ── Step 2: Skills, hooks, shell helpers, CLAUDE.md, scheduled jobs ──────────
step "2/3  Install config"
ccst install-everything --apply

# ── Step 3: Health check ─────────────────────────────────────────────────────
step "3/3  Health check"
ccst doctor

echo ""
echo "Done."
echo "Restart your shell (or: source ~/.bashrc  /  source ~/.zshrc)"
echo "to start using 'ccl' and 'ccl --global'."
echo ""
echo "For future upgrades, you only need:"
echo "  uv tool install cc-session-tools --upgrade"
echo "  (ccst syncs its own config on the next command you run.)"
