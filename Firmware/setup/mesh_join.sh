#!/usr/bin/env bash
# ============================================================================
# Join or leave the EweGo mesh network from a laptop
# ============================================================================
# Uses B.A.T.M.A.N. Advanced (batman-adv) over IBSS (ad-hoc) mode.
# Requires: batctl, iw, ip (batctl installed automatically if missing)
#
# Usage:
#   bash mesh_join.sh join [ip-suffix]   Join as 10.0.0.<suffix> (default: 100)
#   bash mesh_join.sh leave              Disconnect and restore normal WiFi
#   bash mesh_join.sh status             Show mesh neighbors and connectivity
#
# Examples:
#   bash mesh_join.sh join 100           → joins as 10.0.0.100
#   bash mesh_join.sh join               → same (100 is default)
#   bash mesh_join.sh leave
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

ACTION="${1:-join}"
SUFFIX="${2:-100}"
MESH_IP="10.0.0.${SUFFIX}"
IFACE="wlan0"
CELL="02:12:34:56:78:9A"   # Must match all EweGo devices

case "$ACTION" in
    join)
        # Install batctl if missing
        if ! command -v batctl &>/dev/null; then
            info "Installing batctl..."
            sudo apt update -qq && sudo apt install -y batctl
        fi

        # Load batman-adv module
        info "Loading batman-adv kernel module..."
        sudo modprobe batman-adv

        # Take down wlan0 and switch to IBSS mode
        info "Setting $IFACE to ad-hoc (IBSS) mode..."
        sudo ip link set "$IFACE" down
        sudo iw dev "$IFACE" set type ibss
        sudo ip link set "$IFACE" up

        # Join the IBSS cell (2437 MHz = channel 6, 2.4 GHz)
        info "Joining IBSS cell ewego-mesh..."
        sudo iw dev "$IFACE" ibss join ewego-mesh 2437 HT20 fixed-freq "$CELL"

        # Add wlan0 to batman
        info "Adding $IFACE to batman mesh..."
        sudo batctl meshif bat0 if add "$IFACE" 2>/dev/null || true

        # Bring up bat0 and assign IP
        sudo ip link set bat0 up
        sudo ip addr flush dev bat0
        sudo ip addr add "${MESH_IP}/24" dev bat0

        echo ""
        info "Connected to mesh as $MESH_IP (bat0)"
        echo "  Show neighbors:  sudo batctl meshif bat0 n"
        echo "  Ping a device:   ping 10.0.0.1"
        echo "  SSH to ewe1:     ssh william@10.0.0.1"
        echo "  Disconnect:      bash $0 leave"
        ;;

    leave)
        info "Tearing down mesh..."

        # Bring down bat0
        sudo ip addr flush dev bat0 2>/dev/null || true
        sudo ip link set bat0 down 2>/dev/null || true

        # Remove wlan0 from batman
        sudo batctl meshif bat0 if del "$IFACE" 2>/dev/null || true

        # Restore managed mode so NetworkManager can reclaim wlan0
        sudo ip link set "$IFACE" down
        sudo iw dev "$IFACE" set type managed
        sudo ip link set "$IFACE" up

        # Restart NM to reconnect to normal WiFi
        info "Restarting NetworkManager..."
        sudo systemctl restart NetworkManager

        info "Disconnected from mesh — normal WiFi should reconnect shortly"
        ;;

    status)
        echo "=== bat0 Interface ==="
        ip addr show bat0 2>/dev/null || warn "bat0 not found (mesh not active?)"
        echo ""
        echo "=== IBSS Interface ($IFACE) ==="
        iw dev "$IFACE" info 2>/dev/null || warn "$IFACE not found"
        echo ""
        echo "=== Mesh Neighbors ==="
        sudo batctl meshif bat0 n 2>/dev/null || warn "No neighbors (or mesh not active)"
        echo ""
        echo "=== Originator Table ==="
        sudo batctl meshif bat0 o 2>/dev/null || warn "No originators (or mesh not active)"
        ;;

    *)
        error "Unknown action: $ACTION"
        echo "Usage: bash $0 [join|leave|status] [ip-suffix]"
        exit 1
        ;;
esac
