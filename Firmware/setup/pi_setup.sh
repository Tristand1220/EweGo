#!/usr/bin/env bash
# ============================================================================
# EweGo Pi CM4 Setup Script
# ============================================================================
# Sets up a fresh Raspberry Pi CM4 with all sensors:
#   - Dual IMX708 cameras (H.264 @ 1080p30)
#   - BNO055 IMU via UART5
#   - u-blox ZED-X20P GPS via UART3 + UART4
#   - Google AIY Voice Hat (audio recording)
#   - MAX17048 fuel gauge via I2C bus 1
#
# Usage:
#   1. Flash Raspberry Pi OS (Bookworm/Trixie 64-bit) to SD card
#   2. Set user/password during imaging (WiFi config via imager is unreliable,
#      this script will configure it instead)
#   3. Boot the Pi and connect via UART console (GPIO 14/15, 115200 baud)
#   4. Copy this repo to ~/EweGo (or rsync from dev machine)
#   5. Run: bash ~/EweGo/Firmware/setup/pi_setup.sh
#   6. Reboot when prompted
# ============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; }

EWEGO_DIR="$HOME/EweGo"

if [ ! -d "$EWEGO_DIR" ]; then
    error "EweGo directory not found at $EWEGO_DIR"
    echo "  Copy/rsync the repo first, then run this script."
    exit 1
fi

echo "============================================================================"
echo " EweGo Pi CM4 Setup"
echo "============================================================================"
echo ""

# --------------------------------------------------------------------------
# 1. System packages
# --------------------------------------------------------------------------
# Pi OS Trixie uses HTTP apt sources by default; switch to HTTPS to avoid
# failures on networks that block port 80 (phone hotspots, shared connections).
info "Switching apt sources to HTTPS..."
sudo sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
sudo sed -i 's|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g' /etc/apt/sources.list.d/raspi.sources 2>/dev/null || true

info "Updating package index..."
sudo apt update

info "Installing system packages..."
sudo apt install -y --no-install-recommends \
    python3-picamera2 \
    python3-libcamera \
    i2c-tools \
    python3-smbus2

# --------------------------------------------------------------------------
# 2. uv (Python package manager)
# --------------------------------------------------------------------------
if ! command -v uv &>/dev/null && [ ! -f "$HOME/.local/bin/uv" ]; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
else
    info "uv already installed"
fi

# Make uv available in this session
export PATH="$HOME/.local/bin:$PATH"

# --------------------------------------------------------------------------
# 3. Python venv with system site-packages (for picamera2)
# --------------------------------------------------------------------------
info "Creating Python venv with system site-packages..."
cd "$EWEGO_DIR"
uv venv --system-site-packages
uv pip install -r Firmware/requirements.txt

info "Verifying Python packages..."
source .venv/bin/activate
python -c "import serial; print(f'  pyserial: {serial.__version__}')"
python -c "import pyubx2; print(f'  pyubx2: {pyubx2.__version__}')"
python -c "import picamera2; print(f'  picamera2: {picamera2.__version__}')" 2>/dev/null || warn "picamera2 not available (OK if no cameras connected)"
deactivate

# --------------------------------------------------------------------------
# 4. I2C device module (for fuel gauge)
# --------------------------------------------------------------------------
if [ ! -f /etc/modules-load.d/i2c-dev.conf ]; then
    info "Enabling i2c-dev module on boot..."
    echo "i2c-dev" | sudo tee /etc/modules-load.d/i2c-dev.conf
else
    info "i2c-dev already configured"
fi

# --------------------------------------------------------------------------
# 5. Hostname configuration
# --------------------------------------------------------------------------
# EweGo devices use the naming convention ewe1, ewe2, ewe3, ...
# The device number determines the mesh network IP: eweN → 10.42.0.N
CURRENT_HOSTNAME=$(hostname)
if [[ "$CURRENT_HOSTNAME" =~ ^ewe[^0-9]*([0-9]+)$ ]]; then
    DEVICE_NUM=$((10#${BASH_REMATCH[1]}))
    info "Hostname: $CURRENT_HOSTNAME (device #$DEVICE_NUM)"
else
    echo ""
    info "Hostname configuration"
    echo "  Current hostname: $CURRENT_HOSTNAME"
    echo "  EweGo devices use the naming convention: ewe1, ewe2, ewe3, ..."
    echo "  The device number determines the mesh IP: eweN → 10.42.0.N"
    echo ""
    read -r -p "  Device number (1-254): " DEVICE_NUM

    if ! [[ "$DEVICE_NUM" =~ ^[0-9]+$ ]] || [ "$DEVICE_NUM" -lt 1 ] || [ "$DEVICE_NUM" -gt 254 ]; then
        error "Invalid device number: $DEVICE_NUM (must be 1-254)"
        exit 1
    fi

    NEW_HOSTNAME="ewe${DEVICE_NUM}"
    info "Setting hostname to $NEW_HOSTNAME..."
    sudo hostnamectl set-hostname "$NEW_HOSTNAME"

    # Update /etc/hosts so sudo doesn't complain about unresolvable hostname
    if ! grep -q "$NEW_HOSTNAME" /etc/hosts; then
        sudo sed -i "s/127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
        # If no 127.0.1.1 line existed, add one
        if ! grep -q "127.0.1.1" /etc/hosts; then
            echo -e "127.0.1.1\t$NEW_HOSTNAME" | sudo tee -a /etc/hosts > /dev/null
        fi
    fi

    # Stop cloud-init from re-applying the original imager hostname on every
    # boot. Pi OS ships with preserve_hostname=false, which silently reverts
    # any manual rename. Must be flipped on every Pi where we rename.
    if [ -f /etc/cloud/cloud.cfg ] && grep -q "^preserve_hostname" /etc/cloud/cloud.cfg; then
        if ! grep -q "^preserve_hostname: true" /etc/cloud/cloud.cfg; then
            info "Disabling cloud-init hostname reset (preserve_hostname: true)..."
            sudo sed -i 's/^preserve_hostname.*/preserve_hostname: true/' /etc/cloud/cloud.cfg
        fi
    elif [ -f /etc/cloud/cloud.cfg ]; then
        info "Disabling cloud-init hostname reset (preserve_hostname: true)..."
        echo "preserve_hostname: true" | sudo tee -a /etc/cloud/cloud.cfg > /dev/null
    fi

    info "Hostname set (fully active after reboot)"
fi

# --------------------------------------------------------------------------
# 6. USB Ethernet gadget (SSH over USB-C)
# --------------------------------------------------------------------------
# Enables the Pi to appear as a USB Ethernet adapter when connected via USB-C.
# Creates a usb0 interface with a static IP for reliable SSH access.
# This is independent of wlan0/bat0 and doesn't affect mesh networking.
# Placed early so it's configured even if later steps fail or kill SSH.

# Load g_ether module on boot
if ! grep -q "g_ether" /etc/modules-load.d/usb-gadget.conf 2>/dev/null; then
    info "Enabling USB Ethernet gadget on boot..."
    printf "dwc2\ng_ether\n" | sudo tee /etc/modules-load.d/usb-gadget.conf
else
    info "USB Ethernet gadget already configured"
fi

# Configure static IP on usb0 via NetworkManager
USB_CONN_FILE="/etc/NetworkManager/system-connections/usb-gadget.nmconnection"
# Per-device USB subnet: each Pi gets its own /24 so the laptop can host
# multiple USB-C-connected Pis at once without same-subnet routing ambiguity.
USB_IP="10.55.${DEVICE_NUM}.1"
if [ ! -f "$USB_CONN_FILE" ] || ! sudo grep -q "address1=${USB_IP}/24" "$USB_CONN_FILE" 2>/dev/null; then
    info "Configuring USB gadget network (usb0 = $USB_IP)..."
    sudo tee "$USB_CONN_FILE" > /dev/null <<EOF
[connection]
id=usb-gadget
type=ethernet
interface-name=usb0
autoconnect=yes

[ipv4]
method=manual
address1=${USB_IP}/24

[ipv6]
method=link-local
EOF
    sudo chmod 600 "$USB_CONN_FILE"
    # Apply immediately so re-runs on an existing Pi take effect without
    # waiting for reboot. Safe over USB-C — only this one connection cycles.
    sudo nmcli connection reload 2>/dev/null || true
    sudo nmcli connection down usb-gadget 2>/dev/null || true
    sudo nmcli connection up usb-gadget 2>/dev/null || true
else
    info "USB gadget network already configured at $USB_IP/24"
fi

# --------------------------------------------------------------------------
# 7. /boot/firmware/config.txt (hardware overlays)
# --------------------------------------------------------------------------
CONFIG="/boot/firmware/config.txt"
info "Configuring $CONFIG..."

# Back up current config
sudo cp "$CONFIG" "${CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"

# --- USB gadget mode booby-traps in config.txt -----------------------------
# These three blocks run on every invocation (not gated on the imx708 check)
# so re-runs stay idempotent. All three silently break USB-C SSH if left in
# place, and stock Pi OS images can ship with any of them.

# (a) otg_mode=1 — switches the OTG port to the XHCI host controller, which
# disables dwc2 entirely. Common in stock images under the [cm4] section to
# expose the OTG port as an extra USB host. Lethal for gadget mode.
if grep -qE "^[[:space:]]*otg_mode=1[[:space:]]*$" "$CONFIG"; then
    warn "Disabling 'otg_mode=1' — forces XHCI host, blocks USB gadget on CM4"
    sudo sed -i -E 's|^([[:space:]]*otg_mode=1[[:space:]]*)$|#\1  # disabled by pi_setup.sh: conflicts with USB gadget|' "$CONFIG"
fi

# (b) dtoverlay=dwc2,dr_mode=host — puts the dwc2 controller in host-only
# mode, so g_ether finds no UDC to bind to.
if grep -qE "^[[:space:]]*dtoverlay=dwc2.*dr_mode=host" "$CONFIG"; then
    warn "Disabling 'dtoverlay=dwc2,dr_mode=host' — blocks USB gadget mode"
    sudo sed -i -E 's|^([[:space:]]*dtoverlay=dwc2.*dr_mode=host.*)$|#\1  # disabled by pi_setup.sh: conflicts with USB gadget|' "$CONFIG"
fi

# (c) Bare 'dtoverlay=dwc2' — defaults to dr_mode=otg, which relies on the
# OTG_ID pin being pulled correctly. Unreliable across carrier boards. Force
# peripheral mode so the controller comes up as a UDC regardless of hardware.
if grep -qE "^[[:space:]]*dtoverlay=dwc2[[:space:]]*$" "$CONFIG"; then
    warn "Upgrading bare 'dtoverlay=dwc2' → 'dtoverlay=dwc2,dr_mode=peripheral'"
    sudo sed -i -E 's|^([[:space:]]*)dtoverlay=dwc2[[:space:]]*$|\1dtoverlay=dwc2,dr_mode=peripheral|' "$CONFIG"
fi

# Check if our hardware block is already present
if grep -q "dtoverlay=imx708,cam0" "$CONFIG" 2>/dev/null; then
    info "Hardware overlays already configured in config.txt"
else
    warn "Appending hardware overlay configuration to config.txt"
    sudo tee -a "$CONFIG" > /dev/null <<'EOF'

# === EweGo Hardware Configuration ===
[all]
enable_uart=1

# Disable Bluetooth — frees the PL011 UART so the debug console (GPIO 14/15)
# uses a stable clock-independent UART instead of the mini-UART (ttyS0).
# This also prevents GPS data on UART3/4 from interfering with the boot console.
dtoverlay=disable-bt

# Camera configuration (dual IMX708)
camera_auto_detect=0
dtoverlay=imx708,cam0
dtoverlay=imx708,cam1

# Audio configuration (Google AIY Voice Hat)
dtoverlay=googlevoicehat-soundcard

# GPS UART3 + UART4 (u-blox ZED-X20P)
dtoverlay=uart3
dtoverlay=uart4

# IMU UART5 configuration
dtoverlay=uart5

# Fuel Gauge I2C (bus 1 on GPIO 2/3)
dtparam=i2c_arm=on

# GPU memory for H.264 encoding
gpu_mem=256

# USB-C Ethernet gadget (SSH over USB-C cable)
# dr_mode=peripheral is required — without it, the controller defaults to
# 'otg' and depends on the OTG_ID pin being pulled correctly, which varies
# across carrier boards. Forcing peripheral makes gadget mode reliable.
dtoverlay=dwc2,dr_mode=peripheral
EOF
fi

# --------------------------------------------------------------------------
# 8. Summary
# --------------------------------------------------------------------------
echo ""
echo "============================================================================"
echo " Base Setup Complete"
echo "============================================================================"
echo ""
echo " What was configured:"
echo "   - Hostname: ewe${DEVICE_NUM}"
echo "   - USB-C SSH: usb0 = 10.55.${DEVICE_NUM}.1/24 (plug USB-C to laptop)"
echo "   - python3-picamera2, i2c-tools, python3-smbus2 installed via apt"
echo "   - uv + Python venv with pyserial, pyubx2"
echo "   - i2c-dev + dwc2/g_ether kernel modules on boot"
echo "   - config.txt: disable-bt, dual cameras, audio hat, GPS, IMU, fuel gauge"
echo ""
echo " Mesh networking: NOT configured (run mesh_setup.sh to enable)"
echo ""
echo " Hardware pin assignments:"
echo "   GPIO 2/3   - I2C bus 1 (fuel gauge MAX17048 @ 0x36)"
echo "   GPIO 4/5   - UART3 (GPS ZED-X20P secondary)"
echo "   GPIO 8/9   - UART4 (GPS ZED-X20P primary data @ 460800 baud)"
echo "   GPIO 12/13 - UART5 (IMU BNO055)"
echo "   GPIO 14/15 - Debug console (ttyAMA0, 115200 baud) — Bluetooth disabled"
echo "   CAM0/CAM1  - Dual IMX708 cameras"
echo ""
echo " To test sensors after reboot:"
echo "   cd ~/EweGo && uv run python Firmware/sensor_test.py"
echo ""
echo "============================================================================"
echo ""

# --------------------------------------------------------------------------
# 9. Optional: chain into mesh setup
# --------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MESH_SETUP="${SCRIPT_DIR}/mesh_setup.sh"
if [ -f "$MESH_SETUP" ]; then
    read -r -p "Configure B.A.T.M.A.N. mesh networking now? [y/N] " ANSWER
    if [[ "$ANSWER" =~ ^[Yy]$ ]]; then
        echo ""
        bash "$MESH_SETUP" install
    else
        info "Skipped. Run later with: bash ${MESH_SETUP}"
    fi
else
    warn "mesh_setup.sh not found at $MESH_SETUP (mesh setup unavailable)"
fi

echo ""
echo " *** REBOOT REQUIRED for config.txt changes ***"
echo "   Run: sudo reboot"
echo "============================================================================"
