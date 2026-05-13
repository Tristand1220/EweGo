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
    python3-smbus2 \
    batctl

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

# batman-adv kernel module (for mesh networking)
if [ ! -f /etc/modules-load.d/batman-adv.conf ]; then
    info "Enabling batman-adv module on boot..."
    echo "batman-adv" | sudo tee /etc/modules-load.d/batman-adv.conf
else
    info "batman-adv already configured"
fi

# --------------------------------------------------------------------------
# 5. Hostname configuration
# --------------------------------------------------------------------------
# EweGo devices use the naming convention ewe1, ewe2, ewe3, ...
# The device number determines the mesh network IP: eweN → 10.0.0.N
CURRENT_HOSTNAME=$(hostname)
if [[ "$CURRENT_HOSTNAME" =~ ^ewe([0-9]+)$ ]]; then
    DEVICE_NUM="${BASH_REMATCH[1]}"
    info "Hostname: $CURRENT_HOSTNAME (device #$DEVICE_NUM)"
else
    echo ""
    info "Hostname configuration"
    echo "  Current hostname: $CURRENT_HOSTNAME"
    echo "  EweGo devices use the naming convention: ewe1, ewe2, ewe3, ..."
    echo "  The device number determines the mesh IP: eweN → 10.0.0.N"
    echo ""
    read -r -p "  Device number (1-254): " DEVICE_NUM

    if ! [[ "$DEVICE_NUM" =~ ^[0-9]+$ ]] || [ "$DEVICE_NUM" -lt 1 ] || [ "$DEVICE_NUM" -gt 254 ]; then
        error "Invalid device number: $DEVICE_NUM (must be 1-254)"
        exit 1
    fi

    NEW_HOSTNAME="ewe${DEVICE_NUM}"
    info "Setting hostname to $NEW_HOSTNAME..."
    sudo hostnamectl set-hostname "$NEW_HOSTNAME"
    info "Hostname set (fully active after reboot)"
fi

# --------------------------------------------------------------------------
# 6. B.A.T.M.A.N. mesh networking
# --------------------------------------------------------------------------
# The CM4's BCM43455 does NOT support 802.11s mesh mode. Instead we use
# IBSS (ad-hoc) mode as the transport layer with batman-adv for L2 mesh
# routing. A systemd service manages the mesh (not NetworkManager).
MESH_IP="10.0.0.${DEVICE_NUM}"

# Clean up empty/corrupt netplan files left by first-boot auto-config
if [ -d /etc/netplan ]; then
    for f in /etc/netplan/90-NM-*.yaml; do
        [ -f "$f" ] || continue
        if [ ! -s "$f" ]; then
            info "Removing empty netplan file: $f"
            sudo rm -f "$f"
        fi
    done
fi

# Remove old 802.11s mesh profile if present (from previous setup attempts)
sudo rm -f /etc/NetworkManager/system-connections/ewego-mesh.nmconnection

# Tell NetworkManager to leave wlan0 alone (we manage it via systemd)
NM_UNMANAGED="/etc/NetworkManager/conf.d/ewego-unmanaged.conf"
if [ ! -f "$NM_UNMANAGED" ]; then
    info "Configuring NetworkManager to ignore wlan0..."
    sudo mkdir -p /etc/NetworkManager/conf.d
    sudo tee "$NM_UNMANAGED" > /dev/null <<'EOF'
[keyfile]
unmanaged-devices=interface-name:wlan0
EOF
fi

# --- 6a. Mesh startup script ---
MESH_SCRIPT="/usr/local/bin/ewego-mesh-start.sh"
info "Installing mesh startup script ($MESH_SCRIPT)..."
sudo tee "$MESH_SCRIPT" > /dev/null <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

# Derive device number from hostname (eweN → N)
HOSTNAME=$(hostname)
if [[ "$HOSTNAME" =~ ^ewe([0-9]+)$ ]]; then
    DEVICE_NUM="${BASH_REMATCH[1]}"
else
    echo "ERROR: hostname '$HOSTNAME' does not match eweN pattern"
    exit 1
fi

MESH_IP="10.0.0.${DEVICE_NUM}"
IFACE="wlan0"
CELL="02:12:34:56:78:9A"   # Fixed IBSS cell ID — all nodes must match

# Load batman-adv if not already loaded
modprobe batman-adv 2>/dev/null || true

# Set up IBSS (ad-hoc) mode on wlan0
ip link set "$IFACE" down
iw dev "$IFACE" set type ibss
ip link set "$IFACE" up

# Join the IBSS cell (2437 MHz = channel 6, 2.4 GHz)
iw dev "$IFACE" ibss join ewego-mesh 2437 HT20 fixed-freq "$CELL"

# Add wlan0 to batman mesh
batctl meshif bat0 if add "$IFACE" 2>/dev/null || true

# Bring up bat0 and assign static IP
ip link set bat0 up
ip addr flush dev bat0
ip addr add "${MESH_IP}/24" dev bat0

echo "Mesh active: bat0 = ${MESH_IP}/24 (IBSS + batman-adv)"
SCRIPT
sudo chmod 755 "$MESH_SCRIPT"

# --- 6b. Systemd service ---
MESH_SERVICE="/etc/systemd/system/ewego-mesh.service"
info "Installing mesh systemd service ($MESH_SERVICE)..."
sudo tee "$MESH_SERVICE" > /dev/null <<'EOF'
[Unit]
Description=EweGo B.A.T.M.A.N. Mesh Network
After=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/ewego-mesh-start.sh
ExecStop=/usr/bin/ip link set bat0 down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ewego-mesh.service
info "Mesh service enabled (starts on boot)"

# Reload NM so it picks up the unmanaged-devices config
sudo systemctl restart NetworkManager

# --------------------------------------------------------------------------
# 7. /boot/firmware/config.txt (hardware overlays)
# --------------------------------------------------------------------------
CONFIG="/boot/firmware/config.txt"
info "Configuring $CONFIG..."

# Back up current config
sudo cp "$CONFIG" "${CONFIG}.bak.$(date +%Y%m%d_%H%M%S)"

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
EOF
fi

# --------------------------------------------------------------------------
# 8. Summary
# --------------------------------------------------------------------------
echo ""
echo "============================================================================"
echo " Setup Complete"
echo "============================================================================"
echo ""
echo " What was configured:"
echo "   - Hostname: ewe${DEVICE_NUM}"
echo "   - B.A.T.M.A.N. mesh: IBSS=ewego-mesh, bat0 IP=${MESH_IP}/24, channel 6"
echo "   - python3-picamera2, i2c-tools, python3-smbus2, batctl installed via apt"
echo "   - uv + Python venv with pyserial, pyubx2"
echo "   - i2c-dev + batman-adv kernel modules set to load on boot"
echo "   - config.txt: disable-bt, dual cameras, audio hat, GPS, IMU, fuel gauge"
echo ""
echo " Mesh networking (B.A.T.M.A.N. Advanced over IBSS):"
echo "   This device: ewe${DEVICE_NUM} → ${MESH_IP} on bat0"
echo "   All devices on the mesh use 10.0.0.N (where N = device number)"
echo "   Managed by: systemd ewego-mesh.service (not NetworkManager)"
echo "   wlan0 is in ad-hoc (IBSS) mode — infrastructure WiFi not available"
echo "   Verify after reboot:"
echo "     sudo batctl meshif bat0 n      # Show mesh neighbors"
echo "     sudo batctl meshif bat0 o      # Show originator table"
echo "     ip addr show bat0              # Show bat0 IP"
echo "     ping 10.0.0.<other>            # Ping another device"
echo "   Join from laptop:"
echo "     bash Firmware/setup/mesh_join.sh join 100"
echo ""
echo " Hardware pin assignments:"
echo "   GPIO 2/3   - I2C bus 1 (fuel gauge MAX17048 @ 0x36)"
echo "   GPIO 4/5   - UART3 (GPS ZED-X20P secondary)"
echo "   GPIO 8/9   - UART4 (GPS ZED-X20P primary data @ 460800 baud)"
echo "   GPIO 12/13 - UART5 (IMU BNO055)"
echo "   GPIO 14/15 - Debug console (ttyAMA0, 115200 baud) — Bluetooth disabled"
echo "   CAM0/CAM1  - Dual IMX708 cameras"
echo ""
echo " To test after reboot:"
echo "   cd ~/EweGo && uv run python Firmware/sensor_test.py"
echo ""
echo " *** REBOOT REQUIRED for config.txt changes ***"
echo "   Run: sudo reboot"
echo "============================================================================"
