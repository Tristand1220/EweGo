#!/usr/bin/env bash
# ============================================================================
# Join or leave the EweGo mesh network from a laptop
# ============================================================================
# Usage:
#   bash mesh_join.sh join [ip-suffix]   Join as 10.0.0.<suffix> (default: 100)
#   bash mesh_join.sh leave              Disconnect from mesh
#   bash mesh_join.sh status             Show mesh peers and connectivity
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
CON_NAME="ewego-mesh"

case "$ACTION" in
    join)
        # Create the mesh profile if it doesn't exist
        if nmcli connection show "$CON_NAME" &>/dev/null; then
            info "Mesh profile already exists, connecting..."
        else
            info "Creating mesh profile (IP: $MESH_IP)..."
            sudo nmcli connection add \
                type wifi \
                ifname wlan0 \
                con-name "$CON_NAME" \
                wifi.mode mesh \
                wifi.ssid ewego-mesh \
                wifi.band bg \
                wifi.channel 6 \
                ipv4.method manual \
                ipv4.addresses "${MESH_IP}/24" \
                ipv6.method link-local
        fi

        info "Connecting to ewego-mesh..."
        nmcli connection up "$CON_NAME"
        echo ""
        info "Connected as $MESH_IP"
        echo "  Scan for peers:  iw dev wlan0 station dump"
        echo "  Ping a device:   ping 10.0.0.1"
        echo "  SSH to ewe1:     ssh william@ewe1.local"
        echo "  Disconnect:      bash $0 leave"
        ;;

    leave)
        info "Disconnecting from ewego-mesh..."
        nmcli connection down "$CON_NAME" 2>/dev/null || true
        info "Disconnected"
        ;;

    status)
        echo "=== Mesh Connection ==="
        nmcli connection show "$CON_NAME" --active 2>/dev/null || warn "Not connected to mesh"
        echo ""
        echo "=== Interface Info ==="
        iw dev wlan0 info 2>/dev/null || warn "wlan0 not found"
        echo ""
        echo "=== Mesh Peers ==="
        iw dev wlan0 station dump 2>/dev/null || warn "No peers found"
        echo ""
        echo "=== Mesh Paths ==="
        iw dev wlan0 mpath dump 2>/dev/null || warn "No mesh paths"
        ;;

    *)
        error "Unknown action: $ACTION"
        echo "Usage: bash $0 [join|leave|status] [ip-suffix]"
        exit 1
        ;;
esac
