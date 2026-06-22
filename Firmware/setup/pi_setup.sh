#!/usr/bin/env bash
# ============================================================================
# EweGo Pi CM4 Setup Script
# ============================================================================
# Sets up a fresh Raspberry Pi CM4 with all sensors:
#   - Dual IMX708 cameras (H.264 @ 1080p30)
#   - BNO055 IMU via UART5
#   - u-blox ZED-X20P GPS via UART3 + UART4
#   - u-blox ZED-X20P GPS via UART3 + UART4
#   - Google AIY Voice Hat (audio recording)
#   - MAX17048 fuel gauge via I2C bus 1
#
# Usage:
#   1. Flash Raspberry Pi OS (Bookworm/Trixie 64-bit) to SD card
#   2. Set user/password during imaging (WiFi config via imager is unreliable,
#      this script will configure it instead)
#   3. Boot the Pi and connect via UART console (GPIO 14/15, 115200 baud)
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

# Pi OS Trixie uses HTTP apt sources by default; switch to HTTPS to avoid
# failures on networks that block port 80 (phone hotspots, shared connections).
info "Switching apt sources to HTTPS..."
sudo sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true
sudo sed -i 's|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g' /etc/apt/sources.list.d/raspi.sources 2>/dev/null || true

info "Updating package index..."
sudo apt update || warn "apt update failed (no internet?) — continuing with cached indexes"

# systemd-timesyncd conflicts with chrony (both provide time-daemon).
# Remove it first so the chrony install doesn't fail.
if dpkg -l systemd-timesyncd 2>/dev/null | grep -q '^ii'; then
    info "Removing systemd-timesyncd (conflicts with chrony)..."
    sudo apt-get remove -y --purge systemd-timesyncd
fi

info "Installing system packages..."
sudo apt install -y --no-install-recommends \
sudo apt install -y --no-install-recommends \
    python3-picamera2 \
    python3-libcamera \
    python3-libcamera \
    i2c-tools \
    python3-smbus2 \
    chrony \
    pps-tools \
    libportaudio2

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
# Create the venv before uv sync — sync would otherwise create one WITHOUT
# system site-packages, hiding the apt-installed picamera2.
uv venv --system-site-packages
# Install exactly what uv.lock pins (single source of truth: pyproject.toml).
# --frozen: never re-resolve against PyPI, so behavior is deterministic and
# later offline `uv run --no-sync` invocations match this environment.
uv sync --frozen

info "Verifying Python packages..."
source .venv/bin/activate
python -c "import serial; print(f'  pyserial: {serial.__version__}')"
python -c "import pyubx2; print(f'  pyubx2: {pyubx2.__version__}')"
python -c "import numpy; print(f'  numpy: {numpy.__version__}')"
python -c "import sounddevice; print(f'  sounddevice: {sounddevice.__version__}')"
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
# The Pi appears as a USB NCM Ethernet adapter when connected via USB-C.
# NCM, not legacy g_ether (ECM): the host-side cdc_ether driver TX-stalls on
# newer laptop kernels (NETDEV WATCHDOG: transmit queue timed out); cdc_ncm
# does not. usb_gadget_ncm.sh also assigns usb0 = 10.55.<N>.1/24, replacing
# the old NetworkManager profile (NM racing the static IP caused flapping).
# Per-device USB subnet: each Pi gets its own /24 so the laptop can host
# multiple USB-C-connected Pis at once without same-subnet routing ambiguity.
# This is independent of wlan0/bat0 and doesn't affect mesh networking.
# Placed early so it's configured even if later steps fail or kill SSH.

# Kernel modules on boot: dwc2 (UDC) + libcomposite (configfs gadgets).
# Drop legacy g_ether if a previous install added it — it would grab the UDC
# first and the NCM gadget would then fail to bind.
if ! grep -qx "libcomposite" /etc/modules-load.d/usb-gadget.conf 2>/dev/null || \
   grep -q "g_ether" /etc/modules-load.d/usb-gadget.conf 2>/dev/null; then
    info "Enabling USB NCM gadget modules on boot..."
    printf "dwc2\nlibcomposite\n" | sudo tee /etc/modules-load.d/usb-gadget.conf
fi

info "Installing USB NCM gadget service (usb0 = 10.55.${DEVICE_NUM}.1)..."
sudo install -m 755 "$EWEGO_DIR/Firmware/setup/usb_gadget_ncm.sh" /usr/local/sbin/usb_gadget_ncm.sh
sudo install -m 644 "$EWEGO_DIR/Firmware/setup/ewego-usb-gadget.service" /etc/systemd/system/ewego-usb-gadget.service
sudo systemctl daemon-reload
sudo systemctl enable ewego-usb-gadget.service > /dev/null 2>&1

# Tell NM to leave usb0 alone — the gadget service owns it. This file sorts
# alphabetically BEFORE mesh_setup.sh's ewego-unmanaged.conf, so when mesh is
# installed its superset (wlan0+usb0) takes precedence (NM: last file wins).
NM_USB0_CONF="/etc/NetworkManager/conf.d/ewego-gadget-unmanaged.conf"
if [ ! -f "$NM_USB0_CONF" ]; then
    info "Configuring NetworkManager to ignore usb0..."
    sudo mkdir -p /etc/NetworkManager/conf.d
    sudo tee "$NM_USB0_CONF" > /dev/null <<'EOF'
[keyfile]
unmanaged-devices=interface-name:usb0
EOF
    sudo systemctl reload NetworkManager 2>/dev/null || true
fi

# Remove the legacy NM profile from older installs. Deleting the active
# profile strips usb0's IP — if this session is SSH'd over USB-C that would
# kill it mid-script, so immediately re-add the IP by hand (NM ignores usb0
# now, so a manual address is stable until the gadget service owns it).
USB_CONN_FILE="/etc/NetworkManager/system-connections/usb-gadget.nmconnection"
if [ -f "$USB_CONN_FILE" ]; then
    info "Removing legacy usb-gadget NetworkManager profile..."
    sudo nmcli connection delete usb-gadget 2>/dev/null || sudo rm -f "$USB_CONN_FILE"
    # Best-effort only: a USB hiccup here must not abort the rest of setup
    # (chrony + config.txt still need to run), so swallow failures.
    if ip link show usb0 &>/dev/null; then
        sudo ip link set usb0 up || true
        sudo ip addr replace "10.55.${DEVICE_NUM}.1/24" dev usb0 || true
    fi
fi

# Start now if possible. Don't unload a live g_ether — that drops the USB
# link and would kill an SSH session running over USB-C; reboot handles it.
if systemctl is-active --quiet ewego-usb-gadget.service; then
    info "USB NCM gadget already active"
elif lsmod | grep -q "^g_ether"; then
    warn "Legacy g_ether is active — NCM gadget takes over after reboot"
else
    sudo systemctl start ewego-usb-gadget.service \
        || warn "Gadget start failed (OK before reboot) — check: journalctl -u ewego-usb-gadget"
fi

# --------------------------------------------------------------------------
# 7. Chrony (GPS PPS time sync)
# --------------------------------------------------------------------------
CHRONY_CONF_SRC="$EWEGO_DIR/Firmware/setup/chrony.conf"
CHRONY_CONF_DST="/etc/chrony/chrony.conf"

if [ ! -f "$CHRONY_CONF_SRC" ]; then
    warn "chrony.conf not found at $CHRONY_CONF_SRC - skipping chrony configurations"
    warn "Place chrony.conf in Firmware/setup/ and re-run to configure"
else
    info "Deploying chrony configuration..."
    sudo cp "$CHRONY_CONF_SRC" "$CHRONY_CONF_DST"
    sudo chown root:root "$CHRONY_CONF_DST"
    sudo chmod 644 "$CHRONY_CONF_DST"

    # GPS/PPS refclocks require /dev/pps0 (pps-gpio overlay, active after first reboot).
    # Install them into sources.d only when the device already exists so chrony
    # doesn't fatal-error on first boot.
    GPS_CONF_SRC="$EWEGO_DIR/Firmware/setup/chrony-gps.conf"
    GPS_CONF_DST="/etc/chrony/sources.d/gps.conf"
    sudo mkdir -p /etc/chrony/sources.d
    if [ -e /dev/pps0 ]; then
        info "PPS device found — installing GPS/PPS refclocks..."
        sudo cp "$GPS_CONF_SRC" "$GPS_CONF_DST"
        sudo chown root:root "$GPS_CONF_DST"
        sudo chmod 644 "$GPS_CONF_DST"
    else
        sudo rm -f "$GPS_CONF_DST"
        warn "No /dev/pps0 yet — GPS/PPS sources will be enabled after reboot"
    fi

    info "Enabling chrony service..."
    sudo systemctl enable chrony

    info "Starting chrony service..."
    if ! sudo systemctl restart chrony 2>&1; then
        warn "chrony failed to start — check: journalctl -u chrony"
    else
        info "Chrony configured (GPS PPS on GPIO 6 via /dev/pps0, mesh peers 10.42.0.1-16, internet fallback)"
    fi
fi

# --------------------------------------------------------------------------
# 8. /boot/firmware/config.txt (hardware overlays)
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
# mode, so the NCM gadget finds no UDC to bind to.
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

# GPS PPS time pulse (GPIO 6 = TIMEPULSE output from ZED-X20P, per EweGo carrier board)
# Harmless if PPS is not physically wired — /dev/pps0 simply won't appear.
dtoverlay=pps-gpio,gpiopin=6

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
# 9. Summary
# --------------------------------------------------------------------------
echo ""
echo "============================================================================"
echo " Base Setup Complete"
echo "============================================================================"
echo ""
echo " What was configured:"
echo "   - Hostname: ewe${DEVICE_NUM}"
echo "   - USB-C SSH: usb0 = 10.55.${DEVICE_NUM}.1/24 (plug USB-C to laptop)"
echo "   - chrony: GPS PPS (GPIO 6) > mesh peers 10.42.0.1-16 > internet fallback"
echo "   - python3-picamera2, i2c-tools, python3-smbus2, libportaudio2 via apt"
echo "   - uv + Python venv synced from uv.lock (pyserial, pyubx2, numpy, sounddevice)"
echo "   - i2c-dev + dwc2/libcomposite kernel modules (NCM gadget) on boot"
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
# 10. Optional: chain into mesh setup
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