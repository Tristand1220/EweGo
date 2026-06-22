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
#   bash ewego_usb.sh nm-restore        Hand USB NICs back to NetworkManager
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

# Probe the identity of a Pi on a USB iface via IPv6 link-local.
# Works on unconfigured interfaces (no IPv4 needed — fe80 is auto-assigned).
# Prints "N hostname" (e.g. "7 ewego007") on success, returns 1 if not found.
probe_iface_identity() {
    local iface="$1"

    # Ensure link-local is up (interface must be UP even without IPv4)
    sudo ip link set "$iface" up 2>/dev/null || true

    # NetworkManager sets addr_gen_mode=1 on ifaces it considers disconnected,
    # which strips our own fe80. Without a source address the multicast probe
    # can't even leave — restore generation and bounce the iface to get one.
    if ! ip -6 addr show dev "$iface" scope link 2>/dev/null | grep -q fe80; then
        sudo sh -c "echo 0 > /proc/sys/net/ipv6/conf/${iface}/addr_gen_mode" 2>/dev/null || true
        sudo ip link set "$iface" down 2>/dev/null || true
        sudo ip link set "$iface" up 2>/dev/null || true
    fi
    # Wait out duplicate address detection (fe80 is unusable while tentative)
    local i
    for i in 1 2 3 4 5 6; do
        ip -6 addr show dev "$iface" scope link 2>/dev/null | \
            grep -q 'fe80.*tentative' || break
        sleep 0.5
    done

    local own
    own=$(ip -6 addr show dev "$iface" scope link 2>/dev/null | \
        awk '/inet6 fe80/{sub("/.*","",$2); print $2; exit}')
    [ -z "$own" ] && return 1

    # Solicit neighbor advertisements from all nodes on the link, keeping the
    # responder addresses (format: "64 bytes from fe80::x%iface: ...")
    local replies
    replies=$(ping -6 -c2 -W1 "ff02::1%${iface}" 2>/dev/null | \
        awk -F'[ %]' '/bytes from fe80/{print $4}' | sort -u)

    # Find a reachable fe80 neighbor (the Pi's link-local addr). The neighbor
    # table sometimes stays empty even when replies arrive, so fall back to
    # the ping responders themselves (excluding our own echo).
    local remote
    remote=$(ip -6 neigh show dev "$iface" 2>/dev/null | \
        awk '/fe80.*REACHABLE|fe80.*STALE|fe80.*DELAY/{print $1; exit}')
    if [ -z "$remote" ]; then
        remote=$(grep -vxF "$own" <<<"$replies" | head -1)
    fi
    [ -z "$remote" ] && return 1

    # SSH via link-local to get hostname (scope ID required: addr%iface)
    local name
    name=$(ssh -o BatchMode=yes \
               -o ConnectTimeout=3 \
               -o StrictHostKeyChecking=no \
               -o UserKnownHostsFile=/dev/null \
               "${REMOTE_USER}@${remote}%${iface}" \
               hostname 2>/dev/null) || return 1
    name="${name%%.local}"   # strip .local suffix if present
    [ -z "$name" ] && return 1

    local n
    n=$(parse_device_num "$name") || return 1
    echo "$n $name"
}

# NetworkManager treats USB gadget ifaces it considers "disconnected" as fair
# game: it sets addr_gen_mode=1 (stripping the IPv6 link-local the discovery
# probe depends on) and removes manually-assigned IPs. Install a one-time conf
# matching the gadget *drivers* — survives random per-boot MACs and
# port-dependent names — so NM leaves every plugged-in Pi alone.
NM_UNMANAGED_CONF="/etc/NetworkManager/conf.d/ewego-usb-unmanaged.conf"
ensure_nm_unmanaged() {
    command -v nmcli >/dev/null 2>&1 || return 0
    systemctl is-active --quiet NetworkManager 2>/dev/null || return 0
    [ -f "$NM_UNMANAGED_CONF" ] && return 0
    info "Telling NetworkManager to ignore USB gadget ifaces (one-time setup)..."
    printf '[device-ewego-usb]\nmatch-device=driver:cdc_ncm;driver:cdc_ether;driver:rndis_host\nmanaged=0\n' | \
        sudo tee "$NM_UNMANAGED_CONF" > /dev/null
    sudo systemctl reload NetworkManager
    # This match is by driver, not by our specific Pis, so it also covers any
    # ordinary USB-Ethernet dongle (many bind cdc_ncm/cdc_ether). If you plug
    # one in for real internet and NM won't manage it, undo with the line below.
    warn "NetworkManager will now ignore ALL cdc_ncm/cdc_ether/rndis USB NICs on this laptop."
    warn "  Undo anytime with:  bash $0 nm-restore"
    # Undo NM's addr_gen_mode damage and bounce ifaces to regrow fe80 addrs
    local i
    for i in $(find_usb_ifaces); do
        sudo sh -c "echo 0 > /proc/sys/net/ipv6/conf/${i}/addr_gen_mode" 2>/dev/null || true
        sudo ip link set "$i" down 2>/dev/null || true
        sudo ip link set "$i" up 2>/dev/null || true
    done
    sleep 1
}

# Undo ensure_nm_unmanaged: remove the conf and hand the USB NICs back to NM.
cmd_nm_restore() {
    if [ ! -f "$NM_UNMANAGED_CONF" ]; then
        info "NetworkManager is already managing USB NICs (no $NM_UNMANAGED_CONF)."
        return 0
    fi
    sudo -v
    info "Removing $NM_UNMANAGED_CONF..."
    sudo rm -f "$NM_UNMANAGED_CONF"
    if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager 2>/dev/null; then
        sudo systemctl reload NetworkManager
    fi
    info "NetworkManager will manage cdc_ncm/cdc_ether/rndis USB NICs again."
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
            local identity
            if identity=$(probe_iface_identity "$iface"); then
                local disc_n="${identity%% *}"
                local disc_name="${identity#* }"
                printf "  %-22s  no IP  →  %s (Pi #%d)  ${YELLOW}[run 'up %d' to configure]${NC}\n" \
                    "$iface" "$disc_name" "$disc_n" "$disc_n"
            else
                printf "  %-22s  no IP  ${YELLOW}[run 'up auto' or 'up <N>' to configure]${NC}\n" "$iface"
            fi
        fi
    done
    if [ "$found" = 0 ]; then
        echo "  (no USB Ethernet interfaces detected)"
        echo ""
        echo "  Check that the USB-C cable is plugged in and the Pi is booted."
    fi
}

cmd_up_auto() {
    sudo -v
    ensure_nm_unmanaged
    local found=0
    for iface in $(find_usb_ifaces); do
        local cur
        cur=$(ip -4 -br addr show "$iface" 2>/dev/null | awk '{print $3}')
        # Skip ifaces already configured for an EweGo Pi that responds
        if [[ "$cur" =~ ^10\.55\.([0-9]+)\.100/24$ ]]; then
            local existing_n="${BASH_REMATCH[1]}"
            if ping -c1 -W1 "10.55.${existing_n}.1" >/dev/null 2>&1; then
                info "$iface already configured for Pi #$existing_n"
                found=$((found + 1))
                continue
            fi
        fi
        local identity
        if identity=$(probe_iface_identity "$iface"); then
            local n="${identity%% *}"
            local name="${identity#* }"
            info "Discovered $name (Pi #$n) on $iface — configuring..."
            cmd_up "$n"
            found=$((found + 1))
        else
            warn "$iface: no Pi identity found via IPv6 link-local — try 'up <N>' manually"
        fi
    done
    [ "$found" -eq 0 ] && warn "No EweGo Pis discovered" || true
}

cmd_up() {
    local target="$1"
    local n
    n=$(parse_device_num "$target") || { error "Can't parse device number from '$target'"; exit 1; }
    local laptop_ip="10.55.${n}.100/24"
    local pi_ip="10.55.${n}.1"

    # Prime sudo so subsequent ip commands don't each prompt
    sudo -v
    ensure_nm_unmanaged

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
    # Skip ifaces already configured for some OTHER EweGo Pi so we don't strip
    # working setups (e.g. running 'up 7' must not nuke Pi #8's iface IP).
    for iface in "${ifaces[@]}"; do
        local cur
        cur=$(ip -4 -br addr show "$iface" 2>/dev/null | awk '{print $3}')
        if [[ "$cur" =~ ^10\.55\.([0-9]+)\.100/24$ ]] && [ "${BASH_REMATCH[1]}" != "$n" ]; then
            info "  $iface holds 10.55.${BASH_REMATCH[1]}.100/24 (Pi #${BASH_REMATCH[1]}) — skipping"
            continue
        fi
        info "Trying $iface..."
        flush_ipv4_on_iface "$iface"
        sudo ip addr add "$laptop_ip" dev "$iface"
        sleep 2  # USB gadget needs time to be ready after link-up or reboot
        if ping -c3 -W2 "$pi_ip" >/dev/null 2>&1; then
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

# Share the laptop's internet connection to a Pi over USB.
# Enables IP forwarding and adds iptables MASQUERADE on the outbound iface.
# Also sets the default route on the Pi so it sends traffic through the laptop.
cmd_nat() {
    local target="$1"
    local n
    n=$(parse_device_num "$target") || { error "Can't parse device number from '$target'"; exit 1; }
    local pi_ip="10.55.${n}.1"
    local laptop_usb_ip="10.55.${n}.100"

    # Find the USB iface that holds this Pi's subnet
    local usb_iface=""
    for iface in $(find_usb_ifaces); do
        if ip -4 -br addr show "$iface" 2>/dev/null | grep -q "10.55.${n}.100"; then
            usb_iface="$iface"
            break
        fi
    done
    if [ -z "$usb_iface" ]; then
        error "Pi #$n not configured — run 'up $n' first"
        exit 1
    fi

    # Find the laptop's outbound (internet) interface
    local out_iface
    out_iface=$(ip route show default | awk '/default/{print $5; exit}')
    if [ -z "$out_iface" ]; then
        error "No default route found on laptop — no internet to share"
        exit 1
    fi

    sudo -v
    info "Enabling IP forwarding..."
    sudo sysctl -w net.ipv4.ip_forward=1 >/dev/null

    info "Adding NAT rule: $usb_iface → $out_iface..."
    sudo iptables -t nat -C POSTROUTING -o "$out_iface" -j MASQUERADE 2>/dev/null || \
        sudo iptables -t nat -A POSTROUTING -o "$out_iface" -j MASQUERADE
    sudo iptables -C FORWARD -i "$usb_iface" -o "$out_iface" -j ACCEPT 2>/dev/null || \
        sudo iptables -A FORWARD -i "$usb_iface" -o "$out_iface" -j ACCEPT
    sudo iptables -C FORWARD -i "$out_iface" -o "$usb_iface" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
        sudo iptables -A FORWARD -i "$out_iface" -o "$usb_iface" -m state --state RELATED,ESTABLISHED -j ACCEPT

    info "Setting default route on Pi #$n via laptop ($laptop_usb_ip)..."
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 "${REMOTE_USER}@${pi_ip}" \
        "sudo ip route replace default via ${laptop_usb_ip} && echo 'Route set'"

    # The USB gadget link pushes no DNS, so name resolution fails even once
    # routing works. Prefer resolvectl when resolv.conf is resolved-managed
    # (a plain tee would be clobbered); fall back to writing resolv.conf.
    info "Configuring DNS on Pi #$n..."
    ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        -o ConnectTimeout=5 "${REMOTE_USER}@${pi_ip}" \
        "if command -v resolvectl >/dev/null && [ -L /etc/resolv.conf ]; then \
             sudo resolvectl dns usb0 1.1.1.1 8.8.8.8 && echo 'DNS set (resolvectl)'; \
         else \
             printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' | sudo tee /etc/resolv.conf >/dev/null && echo 'DNS set (resolv.conf)'; \
         fi"

    info "Internet sharing active: Pi #$n → $usb_iface → $out_iface"
    info "Test with: ssh ${REMOTE_USER}@${pi_ip} 'curl -s --max-time 5 https://deb.debian.org > /dev/null && echo ok'"
}

# --------------------------------------------------------------------------
ACTION="${1:-list}"
case "$ACTION" in
    list|"")
        cmd_list
        ;;
    up)
        [ $# -lt 2 ] && { error "Usage: $0 up <N|hostname|auto>"; exit 1; }
        if [ "$2" = "auto" ]; then cmd_up_auto; else cmd_up "$2"; fi
        ;;
    down)
        [ $# -lt 2 ] && { error "Usage: $0 down <N|hostname>"; exit 1; }
        cmd_down "$2"
        ;;
    ssh)
        [ $# -lt 2 ] && { error "Usage: $0 ssh <N|hostname>"; exit 1; }
        cmd_ssh "$2"
        ;;
    nat)
        [ $# -lt 2 ] && { error "Usage: $0 nat <N|hostname>"; exit 1; }
        cmd_nat "$2"
        ;;
    nm-restore)
        cmd_nm_restore
        ;;
    *)
        error "Unknown action: $ACTION"
        echo "Usage: bash $0 [list|up|down|ssh|nat|nm-restore] [N|hostname|auto]"
        exit 1
        ;;
esac
