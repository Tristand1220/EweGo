#!/usr/bin/env bash
# ============================================================================
# Fix: SD Card Network Loss After Power Failure
# ============================================================================
# When the Pi loses power (battery death), NetworkManager's netplan config
# files get truncated to 0 bytes. Worse, NM regenerates these 0-byte files
# on every boot, overwriting any netplan-level fix we apply.
#
# This script bypasses netplan entirely and writes NM connection keyfiles
# directly to /etc/NetworkManager/system-connections/, which is what NM
# natively reads. It also removes the corrupted netplan files to prevent
# them from interfering.
#
# Usage:
#   Mount the SD card on a desktop/laptop, then run:
#     sudo bash fix_sd_power_loss.sh
#
# See 001_sd_power_loss_corruption.md for full bug details.
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

# --- Locate mounted SD card partitions ---
ROOTFS=""
BOOTFS=""

for mount in /media/${SUDO_USER:-$USER}/rootfs /media/$USER/rootfs /mnt/rootfs; do
    if [ -d "$mount/etc" ]; then
        ROOTFS="$mount"
        break
    fi
done

for mount in /media/${SUDO_USER:-$USER}/bootfs /media/$USER/bootfs /mnt/bootfs; do
    if [ -f "$mount/network-config" ]; then
        BOOTFS="$mount"
        break
    fi
done

if [ -z "$ROOTFS" ]; then
    error "Could not find mounted rootfs (looked for /media/*/rootfs/etc/)"
    echo "  Mount the SD card and try again."
    exit 1
fi

if [ -z "$BOOTFS" ]; then
    error "Could not find mounted bootfs (looked for /media/*/bootfs/network-config)"
    echo "  Mount the SD card and try again."
    exit 1
fi

info "Found rootfs at: $ROOTFS"
info "Found bootfs at: $BOOTFS"

NETPLAN_DIR="$ROOTFS/etc/netplan"
NM_CONN_DIR="$ROOTFS/etc/NetworkManager/system-connections"

# --- Diagnose current state ---
echo ""
info "Diagnosing network configuration..."

# Check netplan
if [ -d "$NETPLAN_DIR" ]; then
    for f in "$NETPLAN_DIR"/*.yaml; do
        [ -e "$f" ] || continue
        if [ ! -s "$f" ]; then
            warn "Corrupted (0-byte) netplan file: $(basename "$f")"
        else
            info "Netplan file OK: $(basename "$f") ($(wc -c < "$f") bytes)"
        fi
    done
fi

# Check NM connections
NM_COUNT=$(find "$NM_CONN_DIR" -name "*.nmconnection" 2>/dev/null | wc -l)
if [ "$NM_COUNT" -eq 0 ]; then
    warn "No NetworkManager connection profiles found"
else
    info "Found $NM_COUNT NM connection profile(s)"
fi

# --- Read wifi config from boot partition ---
echo ""
info "Reading network config from boot partition..."
echo "  Source: $BOOTFS/network-config"

SSID=$(grep -A1 "access-points:" "$BOOTFS/network-config" | tail -1 | sed 's/.*"\(.*\)".*/\1/' | tr -d '[:space:]')
PASSWORD=$(grep "password:" "$BOOTFS/network-config" | head -1 | sed 's/.*password: *"\(.*\)".*/\1/' | tr -d '[:space:]')

if [ -z "$SSID" ] || [ -z "$PASSWORD" ]; then
    error "Could not parse SSID/password from network-config"
    echo "  You may need to edit this script with your wifi credentials."
    exit 1
fi
info "Found wifi network: $SSID"

# --- Remove corrupted netplan files ---
echo ""
info "Removing corrupted/stale netplan files..."
for f in "$NETPLAN_DIR"/*.yaml; do
    [ -e "$f" ] || continue
    rm -v "$f"
done

# --- Derive device number from hostname ---
echo ""
HOSTNAME_FILE="$ROOTFS/etc/hostname"
PI_HOSTNAME=""
DEVICE_NUM=""

if [ -f "$HOSTNAME_FILE" ]; then
    PI_HOSTNAME=$(cat "$HOSTNAME_FILE" | tr -d '[:space:]')
    info "Device hostname: $PI_HOSTNAME"
fi

if [[ "$PI_HOSTNAME" =~ ^ewe([0-9]+)$ ]]; then
    DEVICE_NUM="${BASH_REMATCH[1]}"
    info "Device number: $DEVICE_NUM (from hostname)"
else
    read -r -p "  Device number for mesh IP 10.0.0.X (1-254): " DEVICE_NUM
    if ! [[ "$DEVICE_NUM" =~ ^[0-9]+$ ]] || [ "$DEVICE_NUM" -lt 1 ] || [ "$DEVICE_NUM" -gt 254 ]; then
        warn "Invalid device number — skipping mesh profile"
        DEVICE_NUM=""
    fi
fi

# --- Write NM connection keyfiles directly (bypasses netplan) ---
echo ""
info "Writing NetworkManager connection profiles..."

mkdir -p "$NM_CONN_DIR"

# Mesh connection (primary — devices auto-connect to each other)
if [ -n "$DEVICE_NUM" ]; then
    MESH_IP="10.0.0.${DEVICE_NUM}"
    cat > "$NM_CONN_DIR/ewego-mesh.nmconnection" << EOF
[connection]
id=ewego-mesh
type=wifi
interface-name=wlan0
autoconnect=yes
autoconnect-priority=10

[wifi]
mode=mesh
band=bg
channel=6
ssid=ewego-mesh

[ipv4]
method=manual
address1=${MESH_IP}/24

[ipv6]
method=link-local
EOF
    chmod 600 "$NM_CONN_DIR/ewego-mesh.nmconnection"
    info "Written: ewego-mesh.nmconnection (mesh, IP=$MESH_IP)"
fi

# Infrastructure wifi connection (low priority fallback)
cat > "$NM_CONN_DIR/ewego-wifi.nmconnection" << EOF
[connection]
id=ewego-wifi
type=wifi
autoconnect=yes
autoconnect-priority=-1

[wifi]
mode=infrastructure
ssid=$SSID

[wifi-security]
key-mgmt=wpa-psk
psk=$PASSWORD

[ipv4]
method=auto

[ipv6]
method=auto
EOF
chmod 600 "$NM_CONN_DIR/ewego-wifi.nmconnection"
info "Written: ewego-wifi.nmconnection (infrastructure, SSID=$SSID)"

# Wired connection
cat > "$NM_CONN_DIR/Wired.nmconnection" << 'EOF'
[connection]
id=Wired
type=ethernet
interface-name=eth0
autoconnect=true

[ipv4]
method=auto

[ipv6]
method=auto
EOF
chmod 600 "$NM_CONN_DIR/Wired.nmconnection"
info "Written: Wired.nmconnection (ethernet)"

# --- Verify ---
echo ""
echo "============================================================================"
info "Fix applied successfully!"
echo "============================================================================"
echo ""
echo "  What was done:"
echo "    - Removed corrupted netplan files"
if [ -n "$DEVICE_NUM" ]; then
echo "    - Wrote NM mesh profile (ewego-mesh, IP=10.0.0.${DEVICE_NUM})"
fi
echo "    - Wrote NM wifi profile for '$SSID' (low priority fallback)"
echo "    - Wrote NM wired profile for eth0"
echo ""
echo "  Next steps:"
echo "    1. Safely eject the SD card"
echo "    2. Insert into the Pi and power on"
echo "    3. Wait ~30 seconds for mesh/wifi to connect"
if [ -n "$DEVICE_NUM" ]; then
echo "    4. From mesh: ssh user@ewe${DEVICE_NUM}.local (or 10.0.0.${DEVICE_NUM})"
else
echo "    4. Try: ssh user@<hostname>.local"
fi
echo ""
echo "  If it still doesn't work:"
echo "    - Connect a monitor + keyboard to see boot output"
echo "    - Check: ls -la /etc/NetworkManager/system-connections/"
echo "    - Check: nmcli device status"
echo "============================================================================"
