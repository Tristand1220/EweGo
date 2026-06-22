#!/usr/bin/env bash
# ============================================================================
# EweGo USB NCM gadget (configfs) — replaces legacy g_ether
# ============================================================================
# Creates an NCM (CDC Network Control Model) USB gadget via configfs.
# NCM uses the host's cdc_ncm driver instead of cdc_ether, which avoids
# a TX-stall bug observed with the ECM/cdc_ether path on newer host kernels
# (NETDEV WATCHDOG: transmit queue timed out, e.g. 7.0.x).
#
# The Pi kernel has no legacy g_ncm module, only the usb_f_ncm function —
# hence configfs + libcomposite.
#
# Also assigns usb0 = 10.55.<N>.1/24 (N from the eweN hostname). This used to
# be a NetworkManager profile, but NM racing the static IP caused flapping,
# so NM is told to ignore usb0 and this script owns the interface instead.
#
# Installed by pi_setup.sh; run at boot via ewego-usb-gadget.service.
# ============================================================================
set -euo pipefail

GADGET=/sys/kernel/config/usb_gadget/ewego

modprobe libcomposite
modprobe dwc2 2>/dev/null || true

# Idempotent: tear down an existing gadget first
if [ -d "$GADGET" ]; then
    echo "" > "$GADGET/UDC" 2>/dev/null || true
    rm -f "$GADGET/configs/c.1/ncm.usb0" 2>/dev/null || true
    rmdir "$GADGET/configs/c.1/strings/0x409" "$GADGET/configs/c.1" \
          "$GADGET/functions/ncm.usb0" "$GADGET/strings/0x409" \
          "$GADGET" 2>/dev/null || true
fi

mkdir -p "$GADGET"
cd "$GADGET"

echo 0x1d6b > idVendor    # Linux Foundation
echo 0x0104 > idProduct   # Multifunction Composite Gadget
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
hostname > strings/0x409/serialnumber
echo "EweGo" > strings/0x409/manufacturer
echo "EweGo USB Network (NCM)" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "NCM network" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

mkdir -p functions/ncm.usb0
ln -sf "$GADGET/functions/ncm.usb0" configs/c.1/

# Bind to the first available UDC (dwc2). The UDC can appear a moment after
# the dwc2 module loads at boot, so poll briefly instead of failing outright.
UDC_NAME=""
for _ in $(seq 1 10); do
    UDC_NAME=$(ls /sys/class/udc 2>/dev/null | head -1)
    [ -n "$UDC_NAME" ] && break
    sleep 0.5
done
[ -n "$UDC_NAME" ] || { echo "No UDC found — is dwc2 in peripheral mode?" >&2; exit 1; }
echo "$UDC_NAME" > UDC

echo "NCM gadget bound to $UDC_NAME"

# --- Static IP: usb0 = 10.55.<N>.1/24, N parsed from the eweN hostname -----
IFNAME=$(cat functions/ncm.usb0/ifname 2>/dev/null || echo usb0)
HOST=$(hostname)
if [[ "$HOST" =~ ^ewe[^0-9]*([0-9]+)$ ]]; then
    N=$((10#${BASH_REMATCH[1]}))
    ip link set "$IFNAME" up
    ip addr replace "10.55.${N}.1/24" dev "$IFNAME"
    echo "$IFNAME = 10.55.${N}.1/24"
else
    echo "WARNING: hostname '$HOST' does not match eweN — $IFNAME left without IP" >&2
fi
