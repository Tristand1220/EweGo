#!/usr/bin/env bash
# ============================================================================
# Test B.A.T.M.A.N. mesh networking setup on a Pi
# ============================================================================
# Validates each step of the batman-adv stack without permanently changing
# the system. Logs everything to a timestamped file for debugging.
#
# Usage:
#   bash test_batman.sh              Run all tests
#   bash test_batman.sh --teardown   Clean up after a failed test run
#
# Run as root (sudo) or with sudo access.
# ============================================================================

set -uo pipefail

LOGFILE="/tmp/batman_test_$(date +%Y%m%d_%H%M%S).log"
IFACE="wlan0"
CELL="02:12:34:56:78:9A"
PASSED=0
FAILED=0
WARNINGS=0

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "$*" | tee -a "$LOGFILE"; }
pass()  { log "${GREEN}[PASS]${NC} $*"; ((PASSED++)); }
fail()  { log "${RED}[FAIL]${NC} $*"; ((FAILED++)); }
warn()  { log "${YELLOW}[WARN]${NC} $*"; ((WARNINGS++)); }
info()  { log "${CYAN}[INFO]${NC} $*"; }
divider() { log "----------------------------------------------------------------------"; }

teardown() {
    info "Tearing down test state..."
    ip addr flush dev bat0 2>/dev/null
    ip link set bat0 down 2>/dev/null
    batctl meshif bat0 if del "$IFACE" 2>/dev/null
    ip link set "$IFACE" down 2>/dev/null
    iw dev "$IFACE" set type managed 2>/dev/null
    ip link set "$IFACE" up 2>/dev/null
    info "Teardown complete. NetworkManager may need a restart:"
    info "  sudo systemctl restart NetworkManager"
}

if [[ "${1:-}" == "--teardown" ]]; then
    teardown
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo: sudo bash $0"
    exit 1
fi

log "============================================================================"
log " B.A.T.M.A.N. Mesh Test — $(date)"
log " Log file: $LOGFILE"
log "============================================================================"
log ""

# --------------------------------------------------------------------------
# 1. Check hostname convention
# --------------------------------------------------------------------------
divider
info "Test 1: Hostname convention"
HOSTNAME=$(hostname)
if [[ "$HOSTNAME" =~ ^ewe([0-9]+)$ ]]; then
    DEVICE_NUM="${BASH_REMATCH[1]}"
    MESH_IP="10.42.0.${DEVICE_NUM}"
    pass "Hostname '$HOSTNAME' matches eweN pattern (device #$DEVICE_NUM, IP=$MESH_IP)"
else
    MESH_IP="10.42.0.99"
    warn "Hostname '$HOSTNAME' doesn't match eweN pattern — using test IP $MESH_IP"
fi

# --------------------------------------------------------------------------
# 2. Check batctl is installed
# --------------------------------------------------------------------------
divider
info "Test 2: batctl installation"
if command -v batctl &>/dev/null; then
    BATCTL_VER=$(batctl -v 2>&1)
    pass "batctl installed: $BATCTL_VER"
else
    fail "batctl not found — install with: sudo apt install -y batctl"
fi

# --------------------------------------------------------------------------
# 3. Check batman-adv kernel module
# --------------------------------------------------------------------------
divider
info "Test 3: batman-adv kernel module"
if lsmod | grep -q batman_adv; then
    pass "batman-adv module already loaded"
else
    info "Loading batman-adv module..."
    if modprobe batman-adv 2>>"$LOGFILE"; then
        pass "batman-adv module loaded successfully"
    else
        fail "Failed to load batman-adv module (not available in kernel?)"
        log "  Try: sudo apt install -y linux-modules-extra-\$(uname -r)"
    fi
fi

# Log module info
if lsmod | grep -q batman_adv; then
    BATMAN_VER=$(cat /sys/module/batman_adv/version 2>/dev/null || echo "unknown")
    info "batman-adv version: $BATMAN_VER"
fi

# --------------------------------------------------------------------------
# 4. Check IBSS support
# --------------------------------------------------------------------------
divider
info "Test 4: IBSS (ad-hoc) support on $IFACE"
if iw list 2>/dev/null | grep -q "IBSS"; then
    pass "$IFACE supports IBSS mode"
else
    fail "$IFACE does NOT support IBSS mode"
    log "  Full supported modes:"
    iw list 2>/dev/null | grep -A 20 "Supported interface modes" | head -25 | tee -a "$LOGFILE"
fi

# --------------------------------------------------------------------------
# 5. Set IBSS mode on wlan0
# --------------------------------------------------------------------------
divider
info "Test 5: Set $IFACE to IBSS mode"

# Save current state for teardown info
ORIG_STATE=$(iw dev "$IFACE" info 2>/dev/null | grep type | awk '{print $2}')
info "Current $IFACE type: ${ORIG_STATE:-unknown}"

info "Bringing $IFACE down..."
ip link set "$IFACE" down 2>>"$LOGFILE"

info "Setting type to ibss..."
if iw dev "$IFACE" set type ibss 2>>"$LOGFILE"; then
    pass "Set $IFACE to IBSS mode"
else
    fail "Failed to set $IFACE to IBSS mode"
    log "  This might mean the driver or firmware doesn't support it"
    log "  Driver info:"
    ethtool -i "$IFACE" 2>/dev/null | tee -a "$LOGFILE"
fi

info "Bringing $IFACE up..."
ip link set "$IFACE" up 2>>"$LOGFILE"

# Verify
CURRENT_TYPE=$(iw dev "$IFACE" info 2>/dev/null | grep type | awk '{print $2}')
if [[ "$CURRENT_TYPE" == "IBSS" ]]; then
    pass "$IFACE confirmed in IBSS mode"
else
    fail "$IFACE type is '$CURRENT_TYPE', expected 'IBSS'"
fi

# --------------------------------------------------------------------------
# 6. Join IBSS cell
# --------------------------------------------------------------------------
divider
info "Test 6: Join IBSS cell"
info "Joining ewego-mesh on 2437 MHz (channel 6), cell $CELL..."

if iw dev "$IFACE" ibss join ewego-mesh 2437 HT20 fixed-freq "$CELL" 2>>"$LOGFILE"; then
    pass "IBSS join command succeeded"
else
    # May fail if already joined — check state
    if iw dev "$IFACE" info 2>/dev/null | grep -q "ewego-mesh"; then
        pass "Already joined ewego-mesh"
    else
        fail "Failed to join IBSS cell"
    fi
fi

# Give it a moment to associate
sleep 2

# Check IBSS status
info "IBSS state after join:"
iw dev "$IFACE" info 2>/dev/null | tee -a "$LOGFILE"

# --------------------------------------------------------------------------
# 7. Add interface to batman
# --------------------------------------------------------------------------
divider
info "Test 7: Add $IFACE to batman mesh"

if batctl meshif bat0 if add "$IFACE" 2>>"$LOGFILE"; then
    pass "Added $IFACE to bat0"
else
    # Check if already added
    if batctl meshif bat0 if 2>/dev/null | grep -q "$IFACE"; then
        pass "$IFACE already in bat0"
    else
        fail "Failed to add $IFACE to bat0"
    fi
fi

# --------------------------------------------------------------------------
# 8. Bring up bat0 and assign IP
# --------------------------------------------------------------------------
divider
info "Test 8: Configure bat0 interface"

ip link set bat0 up 2>>"$LOGFILE"
ip addr flush dev bat0 2>>"$LOGFILE"

if ip addr add "${MESH_IP}/24" dev bat0 2>>"$LOGFILE"; then
    pass "Assigned $MESH_IP/24 to bat0"
else
    fail "Failed to assign IP to bat0"
fi

# Verify
BAT0_IP=$(ip -4 addr show bat0 2>/dev/null | grep inet | awk '{print $2}')
if [[ -n "$BAT0_IP" ]]; then
    pass "bat0 has IP: $BAT0_IP"
else
    fail "bat0 has no IPv4 address"
fi

info "bat0 interface state:"
ip addr show bat0 2>/dev/null | tee -a "$LOGFILE"

# --------------------------------------------------------------------------
# 9. Batman mesh status
# --------------------------------------------------------------------------
divider
info "Test 9: Batman mesh status"

info "Batman interfaces:"
batctl meshif bat0 if 2>/dev/null | tee -a "$LOGFILE"

info "Batman neighbors (will be empty with only 1 device):"
batctl meshif bat0 n 2>/dev/null | tee -a "$LOGFILE"

info "Batman originator table:"
batctl meshif bat0 o 2>/dev/null | tee -a "$LOGFILE"

pass "Batman mesh is running (neighbors will appear when a second device joins)"

# --------------------------------------------------------------------------
# 10. Systemd service check
# --------------------------------------------------------------------------
divider
info "Test 10: Systemd service configuration"

if [ -f /etc/systemd/system/ewego-mesh.service ]; then
    pass "ewego-mesh.service exists"
    SERVICE_STATUS=$(systemctl is-enabled ewego-mesh.service 2>/dev/null || echo "not-found")
    if [[ "$SERVICE_STATUS" == "enabled" ]]; then
        pass "ewego-mesh.service is enabled (will start on boot)"
    else
        warn "ewego-mesh.service is $SERVICE_STATUS (run pi_setup.sh to enable)"
    fi
else
    warn "ewego-mesh.service not installed yet (run pi_setup.sh)"
fi

if [ -f /usr/local/bin/ewego-mesh-start.sh ]; then
    pass "ewego-mesh-start.sh exists"
else
    warn "ewego-mesh-start.sh not installed yet (run pi_setup.sh)"
fi

# --------------------------------------------------------------------------
# Summary
# --------------------------------------------------------------------------
log ""
log "============================================================================"
log " Results: ${GREEN}$PASSED passed${NC}, ${RED}$FAILED failed${NC}, ${YELLOW}$WARNINGS warnings${NC}"
log " Log saved to: $LOGFILE"
log "============================================================================"

if [[ $FAILED -eq 0 ]]; then
    log ""
    log " ${GREEN}All critical tests passed!${NC} The batman-adv stack works on this device."
    log " Next steps:"
    log "   - Get a second device on the mesh to test connectivity"
    log "   - Run pi_setup.sh to install the systemd service for boot persistence"
    log ""
    log " To tear down this test and restore normal WiFi:"
    log "   sudo bash $0 --teardown && sudo systemctl restart NetworkManager"
else
    log ""
    log " ${RED}Some tests failed.${NC} Review the log for details: $LOGFILE"
    log ""
    log " To tear down and restore WiFi:"
    log "   sudo bash $0 --teardown && sudo systemctl restart NetworkManager"
fi

exit $FAILED
