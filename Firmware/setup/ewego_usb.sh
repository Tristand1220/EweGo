#!/usr/bin/env bash
# ============================================================================
# EweGo USB-C SSH Helper (laptop side)
# ============================================================================
# Manages laptop-side IP configuration for EweGo Pis connected via USB-C.
# Each Pi advertises a /24 subnet on its usb0:  10.55.<DEVICE_NUM>.1/24
# This script assigns the matching 10.55.<N>.100/24 to the laptop so multiple
# Pis can be plugged in at once without subnet collisions.
#
# Usage:
#   bash ewego_usb.sh                   List connected USB-C ifaces and state
#   bash ewego_usb.sh list              Same
#   bash ewego_usb.sh up <N|hostname>   Configure laptop for that Pi
#   bash ewego_usb.sh down <N|hostname> Remove that Pi's laptop IP
#   bash ewego_usb.sh ssh <N|hostname>  Configure (if needed) then SSH
#
# Examples:
#   bash ewego_usb.sh ssh 7             SSH to Pi #7 at 10.55.7.1
#   bash ewego_usb.sh ssh ewego007      Same — accepts the full hostname
#   bash ewego_usb.sh up 2              Set up Pi #2 IP without SSH
#
# Discovery strategy:
#   When multiple USB Ethernet ifaces are present, `up <N>` tries each in turn:
#   removes stale IPs, assigns 10.55.<N>.100/24, pings 10.55.<N>.1. Whichever
#   iface yields a ping response is the one connected to Pi #N. No IPv6 link-
#   local SSH probe needed (avoids sudo-for-route hassles).
#
# Requirements:
#   - sudo for `ip addr add/del` (the script primes sudo at start)
#   - SSH key auth to the Pi (default user is 'user'; override via EWEGO_USER)
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; }

REMOTE_USER="${EWEGO_USER:-user}"

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
# Find all USB Ethernet ifaces — covers the common Linux drivers that bind
# to USB CDC-Ethernet / RNDIS endpoints (which is what g_ether shows up as).
find_usb_ifaces() {
    for d in /sys/class/net/*/; do
        ifname=$(basename "$d")
        [ "$ifname" = "lo" ] && continue
        driver=$(readlink "$d/device/driver" 2>/dev/null | sed 's|.*/||')
        case "$driver" in
            cdc_ether|rndis_host|cdc_ncm|cdc_subset)
                echo "$ifname"
                ;;
        esac
    done
}

# Parse a device number from either an integer or an ewe* hostname.
# Accepts: 7, 007, ewe7, ewe007, ewego7, ewego007
parse_device_num() {
    local s="$1"
    if [[ "$s" =~ ^[0-9]+$ ]]; then
        echo "$((10#$s))"
        return 0
    fi
    if [[ "$s" =~ ^ewe[^0-9]*([0-9]+)$ ]]; then
        echo "$((10#${BASH_REMATCH[1]}))"
        return 0
    fi
    return 1
}

# Remove all IPv4 addresses from an iface. Used before assigning a new one.
flush_ipv4_on_iface() {
    local iface="$1"
    local addrs
    addrs=$(ip -4 -br addr show "$iface" 2>/dev/null | awk '{for(i=3;i<=NF;i++) print $i}')
    for a in $addrs; do
        sudo ip addr del "$a" dev "$iface" 2>/dev/null || true
    done
}

# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
cmd_list() {
    echo "USB Ethernet interfaces:"
    echo ""
    local found=0
    for iface in $(find_usb_ifaces); do
        found=$((found + 1))
        local addr
        addr=$(ip -4 -br addr show "$iface" 2>/dev/null | awk '{print $3}')
        if [[ "$addr" =~ ^10\.55\.([0-9]+)\.100/24$ ]]; then
            local n="${BASH_REMATCH[1]}"
            local pi_ip="10.55.${n}.1"
            if ping -c1 -W1 "$pi_ip" >/dev/null 2>&1; then
                # Try to grab hostname over SSH for clarity (best-effort)
                local h
                h=$(ssh -o BatchMode=yes -o ConnectTimeout=2 \
                    "${REMOTE_USER}@${pi_ip}" hostname 2>/dev/null) || h="?"
                printf "  %-22s  %s  →  Pi #%d (%s)  ${GREEN}[reachable]${NC}\n" \
                    "$iface" "$addr" "$n" "$h"
            else
                printf "  %-22s  %s  →  Pi #%d expected at %s  ${YELLOW}[no response]${NC}\n" \
                    "$iface" "$addr" "$n" "$pi_ip"
            fi
        elif [ -n "$addr" ]; then
            printf "  %-22s  %s  ${YELLOW}[non-EweGo subnet — run 'up <N>' to reconfigure]${NC}\n" "$iface" "$addr"
        else
            printf "  %-22s  no IP  ${YELLOW}[run 'up <N>' to configure]${NC}\n" "$iface"
        fi
    done
    if [ "$found" = 0 ]; then
        echo "  (no USB Ethernet interfaces detected)"
        echo ""
        echo "  Check that the USB-C cable is plugged in and the Pi is booted."
    fi
}

cmd_up() {
    local target="$1"
    local n
    n=$(parse_device_num "$target") || { error "Can't parse device number from '$target'"; exit 1; }
    local laptop_ip="10.55.${n}.100/24"
    local pi_ip="10.55.${n}.1"

    # Prime sudo so subsequent ip commands don't each prompt
    sudo -v

    local ifaces
    ifaces=($(find_usb_ifaces))
    if [ ${#ifaces[@]} -eq 0 ]; then
        error "No USB Ethernet ifaces found (is the cable plugged in?)"
        exit 1
    fi

    # Fast path: if some iface is already configured for Pi #N and responds, done.
    for iface in "${ifaces[@]}"; do
        local cur
        cur=$(ip -4 -br addr show "$iface" 2>/dev/null | awk '{print $3}')
        if [ "$cur" = "$laptop_ip" ]; then
            if ping -c1 -W1 "$pi_ip" >/dev/null 2>&1; then
                info "$iface already configured for Pi #$n"
                echo "  Laptop: ${laptop_ip%/*}    Pi: $pi_ip"
                echo "  SSH:    ssh ${REMOTE_USER}@${pi_ip}"
                return 0
            fi
        fi
    done

    # Brute-force discovery: try each iface, assign IP, ping. Keep on success.
    for iface in "${ifaces[@]}"; do
        info "Trying $iface..."
        flush_ipv4_on_iface "$iface"
        sudo ip addr add "$laptop_ip" dev "$iface"
        sleep 0.5
        if ping -c2 -W1 "$pi_ip" >/dev/null 2>&1; then
            info "Found Pi #$n on $iface"
            echo "  Laptop: ${laptop_ip%/*}    Pi: $pi_ip"
            echo "  SSH:    ssh ${REMOTE_USER}@${pi_ip}"
            return 0
        fi
        warn "  Pi #$n not on $iface — cleaning up and trying next"
        sudo ip addr del "$laptop_ip" dev "$iface" 2>/dev/null || true
    done

    error "Pi #$n not reachable on any USB iface. Possible causes:"
    echo "    - Pi #$n is not plugged in (check 'bash $0 list')"
    echo "    - Pi's usb0 doesn't have $pi_ip (check Pi side: 'ip addr show usb0')"
    echo "    - Pi hasn't run pi_setup.sh with the new per-device subnet scheme"
    exit 1
}

cmd_down() {
    local target="$1"
    local n
    n=$(parse_device_num "$target") || { error "Can't parse device number from '$target'"; exit 1; }
    local laptop_ip="10.55.${n}.100/24"

    sudo -v

    local found=0
    for iface in $(find_usb_ifaces); do
        if ip -4 -br addr show "$iface" 2>/dev/null | grep -q "$laptop_ip"; then
            sudo ip addr del "$laptop_ip" dev "$iface"
            info "Removed $laptop_ip from $iface"
            found=1
        fi
    done
    [ "$found" = 0 ] && warn "$laptop_ip not present on any USB iface"
}

cmd_ssh() {
    local target="$1"
    local n
    n=$(parse_device_num "$target") || { error "Can't parse device number from '$target'"; exit 1; }
    cmd_up "$target"
    echo ""
    exec ssh "${REMOTE_USER}@10.55.${n}.1"
}

# --------------------------------------------------------------------------
ACTION="${1:-list}"
case "$ACTION" in
    list|"")
        cmd_list
        ;;
    up)
        [ $# -lt 2 ] && { error "Usage: $0 up <N|hostname>"; exit 1; }
        cmd_up "$2"
        ;;
    down)
        [ $# -lt 2 ] && { error "Usage: $0 down <N|hostname>"; exit 1; }
        cmd_down "$2"
        ;;
    ssh)
        [ $# -lt 2 ] && { error "Usage: $0 ssh <N|hostname>"; exit 1; }
        cmd_ssh "$2"
        ;;
    *)
        error "Unknown action: $ACTION"
        echo "Usage: bash $0 [list|up|down|ssh] [N|hostname]"
        exit 1
        ;;
esac
