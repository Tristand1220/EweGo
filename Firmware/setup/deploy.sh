#!/usr/bin/env bash
# ============================================================================
# EweGo Deployment Script
# ============================================================================
# Rsyncs the EweGo firmware to a Pi and optionally runs the setup script.
#
# Usage:
#   bash deploy.sh [user@]<hostname-or-ip>
#
# Examples:
#   bash deploy.sh william@ewe1.local        # SSH-style user@host
#   bash deploy.sh ewe1.local                # Defaults user to $(whoami)
#   bash deploy.sh william@10.42.0.1          # Over mesh (direct IP)
#   bash deploy.sh pi@192.168.1.42           # Over infrastructure WiFi
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
    error "Usage: bash deploy.sh [user@]<hostname-or-ip>"
    echo "  Examples:"
    echo "    bash deploy.sh william@ewe1.local"
    echo "    bash deploy.sh ewe1.local           # user defaults to $(whoami)"
    exit 1
fi

TARGET="$1"
if [[ "$TARGET" == *@* ]]; then
    TARGET_USER="${TARGET%@*}"
    TARGET_HOST="${TARGET#*@}"
else
    TARGET_USER="$(whoami)"
    TARGET_HOST="$TARGET"
    TARGET="${TARGET_USER}@${TARGET_HOST}"
fi

if [ -z "$TARGET_USER" ] || [ -z "$TARGET_HOST" ]; then
    error "Invalid target: '$1' — expected [user@]hostname"
    exit 1
fi

# --------------------------------------------------------------------------
# Resolve hostname → IP ourselves, so we don't depend on the calling shell
# having a working ssh wrapper or correctly-configured nss-mdns.
# --------------------------------------------------------------------------
resolve_host() {
    local host="$1" ip=""

    # Already an IPv4 literal — nothing to do
    if [[ "$host" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
        echo "$host"
        return 0
    fi

    # Prefer IPv4 over IPv6. rsync's host:path syntax can't safely carry an
    # IPv6 address (the colons collide with the path separator), and some
    # avahi configs tag global v6 addresses with a scope-id like '%3' which
    # makes ssh/rsync command lines fragile. Use ahostsv4 first.
    ip=$(getent ahostsv4 "$host" 2>/dev/null | awk '/STREAM/{print $1; exit}')
    if [ -n "$ip" ]; then
        echo "$ip"
        return 0
    fi

    # mDNS fallback for .local names when nss-mdns isn't set up
    if [[ "$host" == *.local ]] && command -v avahi-resolve &>/dev/null; then
        ip=$(avahi-resolve -n4 "$host" 2>/dev/null | awk '{print $2}')
        [ -n "$ip" ] && { echo "$ip"; return 0; }
    fi

    # Last resort: IPv6 (will likely fail with rsync, but better than nothing)
    ip=$(getent ahostsv6 "$host" 2>/dev/null | awk '/STREAM/{print $1; exit}')
    if [ -n "$ip" ]; then
        echo "$ip"
        return 0
    fi

    return 1
}

if ! TARGET_IP=$(resolve_host "$TARGET_HOST"); then
    error "Could not resolve '$TARGET_HOST' to an IP."
    echo "  Tried: getent ahosts $TARGET_HOST"
    if [[ "$TARGET_HOST" == *.local ]]; then
        if command -v avahi-resolve &>/dev/null; then
            echo "  Tried: avahi-resolve -n4 $TARGET_HOST"
        else
            echo "  avahi-resolve not installed:"
            echo "    Arch/Manjaro:   sudo pacman -S avahi"
            echo "    Debian/Ubuntu:  sudo apt install avahi-utils"
        fi
    fi
    echo "  Or pass the IP directly: bash $0 ${TARGET_USER}@<ip>"
    exit 1
fi

# Use the resolved IP for actual network ops; keep $TARGET for user-facing display
TARGET_SSH="${TARGET_USER}@${TARGET_IP}"

TARGET_DIR="~/EweGo"

# Resolve repo root (two levels up from this script: setup/ -> Firmware/ -> EweGo/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RSYNC_IGNORE="$REPO_ROOT/.rsyncignore"

echo "============================================================================"
echo " EweGo Deploy"
echo "============================================================================"
echo "  Source : $REPO_ROOT/"
if [ "$TARGET_HOST" = "$TARGET_IP" ]; then
    echo "  Target : $TARGET:$TARGET_DIR"
else
    echo "  Target : $TARGET:$TARGET_DIR  (resolved → $TARGET_IP)"
fi
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
    "$TARGET_SSH:$TARGET_DIR"

echo ""
info "Sync complete."

# --------------------------------------------------------------------------
# Optional: run pi_setup.sh on the remote
# --------------------------------------------------------------------------
echo ""
read -r -p "Run pi_setup.sh on $TARGET_HOST now? [y/N] " RUN_SETUP
if [[ "$RUN_SETUP" =~ ^[Yy]$ ]]; then
    info "Running pi_setup.sh on $TARGET_HOST..."
    # -t allocates a TTY so sudo can prompt for a password
    ssh -t "$TARGET_SSH" "bash $TARGET_DIR/Firmware/setup/pi_setup.sh"
    echo ""
    read -r -p "Reboot $TARGET_HOST now? [y/N] " DO_REBOOT
    if [[ "$DO_REBOOT" =~ ^[Yy]$ ]]; then
        info "Rebooting $TARGET_HOST..."
        ssh -t "$TARGET_SSH" "sudo reboot" || true
    else
        warn "Remember to reboot for config.txt changes to take effect."
    fi
else
    info "Skipping setup. To run manually:"
    echo "  ssh -t $TARGET_SSH \"bash $TARGET_DIR/Firmware/setup/pi_setup.sh\""
fi

echo ""
echo "============================================================================"
echo " Done"
echo "============================================================================"
