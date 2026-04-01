#!/usr/bin/env bash
# ============================================================================
# EweGo Deployment Script
# ============================================================================
# Rsyncs the EweGo firmware to a Pi and optionally runs the setup script.
#
# Usage:
#   bash deploy.sh <hostname-or-ip> [user]
#
# Examples:
#   bash deploy.sh pi4.local
#   bash deploy.sh pi4.local william
#   bash deploy.sh 192.168.1.42 pi
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

# --------------------------------------------------------------------------
# Args
# --------------------------------------------------------------------------
if [ $# -lt 1 ]; then
    error "Usage: bash deploy.sh <hostname-or-ip> [user]"
    echo "  Examples:"
    echo "    bash deploy.sh pi4.local"
    echo "    bash deploy.sh pi4.local william"
    exit 1
fi

TARGET_HOST="$1"
TARGET_USER="${2:-$(whoami)}"
TARGET="${TARGET_USER}@${TARGET_HOST}"
TARGET_DIR="~/EweGo"

# Resolve repo root (two levels up from this script: setup/ -> Firmware/ -> EweGo/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RSYNC_IGNORE="$REPO_ROOT/.rsyncignore"

echo "============================================================================"
echo " EweGo Deploy"
echo "============================================================================"
echo "  Source : $REPO_ROOT/"
echo "  Target : $TARGET:$TARGET_DIR"
echo "============================================================================"
echo ""

if [ ! -f "$RSYNC_IGNORE" ]; then
    warn ".rsyncignore not found at $RSYNC_IGNORE — syncing without excludes"
    EXCLUDE_ARG=""
else
    EXCLUDE_ARG="--exclude-from=$RSYNC_IGNORE"
fi

# --------------------------------------------------------------------------
# Rsync
# --------------------------------------------------------------------------
info "Syncing firmware..."
rsync -avz --progress \
    $EXCLUDE_ARG \
    "$REPO_ROOT/" \
    "$TARGET:$TARGET_DIR"

echo ""
info "Sync complete."

# --------------------------------------------------------------------------
# Optional: run pi_setup.sh on the remote
# --------------------------------------------------------------------------
echo ""
read -r -p "Run pi_setup.sh on $TARGET_HOST now? [y/N] " RUN_SETUP
if [[ "$RUN_SETUP" =~ ^[Yy]$ ]]; then
    info "Running pi_setup.sh on $TARGET_HOST..."
    ssh "$TARGET" "bash $TARGET_DIR/Firmware/setup/pi_setup.sh"
    echo ""
    read -r -p "Reboot $TARGET_HOST now? [y/N] " DO_REBOOT
    if [[ "$DO_REBOOT" =~ ^[Yy]$ ]]; then
        info "Rebooting $TARGET_HOST..."
        ssh "$TARGET" "sudo reboot" || true
    else
        warn "Remember to reboot for config.txt changes to take effect."
    fi
else
    info "Skipping setup. To run manually:"
    echo "  ssh $TARGET \"bash $TARGET_DIR/Firmware/setup/pi_setup.sh\""
fi

echo ""
echo "============================================================================"
echo " Done"
echo "============================================================================"
