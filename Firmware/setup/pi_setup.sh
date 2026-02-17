#!/usr/bin/env bash
# ============================================================================
# EweGo Pi CM4 Setup Script
# ============================================================================
# Sets up a fresh Raspberry Pi CM4 with all sensors:
#   - Dual IMX708 cameras (H.264 @ 1080p30)
#   - BNO055 IMU via UART5
#   - u-blox ZED-X20P GPS via UART4
#   - Google AIY Voice Hat (audio recording)
#   - MAX17048 fuel gauge via I2C bus 1
#
# Usage:
#   1. Flash Raspberry Pi OS (Bookworm/Trixie 64-bit) to SD card
#   2. Enable SSH, set user/password during imaging
#   3. Boot the Pi and SSH in
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
info "Updating package index..."
sudo apt update

info "Installing system packages..."
sudo apt install -y \
    python3-picamera2 \
    i2c-tools \
    python3-smbus2 \
    ffmpeg

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
uv pip install -r requirements.txt

info "Verifying Python packages..."
source .venv/bin/activate
python -c "import cv2; print(f'  opencv: {cv2.__version__}')"
python -c "import picamera2; print(f'  picamera2: {picamera2.__version__}')" 2>/dev/null || warn "picamera2 not available (OK if no cameras connected)"
python -c "import serial; print(f'  pyserial: {serial.__version__}')"
python -c "import pyubx2; print(f'  pyubx2: {pyubx2.__version__}')"
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
# 5. /boot/firmware/config.txt
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

# Camera configuration (dual IMX708)
camera_auto_detect=0
dtoverlay=imx708,cam0
dtoverlay=imx708,cam1

# Audio configuration (Google AIY Voice Hat)
dtoverlay=googlevoicehat-soundcard

# GPS UART4 configuration
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
# 6. Summary
# --------------------------------------------------------------------------
echo ""
echo "============================================================================"
echo " Setup Complete"
echo "============================================================================"
echo ""
echo " What was configured:"
echo "   - python3-picamera2, i2c-tools, ffmpeg installed via apt"
echo "   - uv + Python venv with opencv, pyserial, pyubx2"
echo "   - i2c-dev kernel module set to load on boot"
echo "   - config.txt: dual cameras, audio hat, GPS, IMU, fuel gauge, gpu_mem"
echo ""
echo " Hardware pin assignments:"
echo "   GPIO 2/3   - I2C bus 1 (fuel gauge MAX17048 @ 0x36)"
echo "   GPIO 8/9   - UART4 (GPS ZED-X20P @ 460800 baud)"
echo "   GPIO 12/13 - UART5 (IMU BNO055)"
echo "   CAM0/CAM1  - Dual IMX708 cameras"
echo ""
echo " To test after reboot:"
echo "   cd ~/EweGo && source .venv/bin/activate"
echo "   python Firmware/sensor_test.py          # Run all sensors"
echo "   python Firmware/dualcam/dual_cam_jp2.py # Camera only"
echo "   python Firmware/IMU/log_imu_data.py     # IMU only"
echo "   python Firmware/fuel_gauge/max17048_test.py  # Fuel gauge only"
echo "   sudo i2cdetect -y 1                     # Verify I2C bus"
echo ""
echo " *** REBOOT REQUIRED for config.txt changes ***"
echo "   Run: sudo reboot"
echo "============================================================================"
