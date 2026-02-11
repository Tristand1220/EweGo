#!/bin/bash
# BNO055 IMU Setup Script for Raspberry Pi
# Simple and straightforward setup for UART5 on /dev/ttyAMA5

set -e

echo "=========================================="
echo "  BNO055 IMU Setup for Raspberry Pi"
echo "=========================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if running as root for config.txt modifications
if [ "$EUID" -eq 0 ]; then
    echo -e "${YELLOW}Warning: Running as root. This is okay for initial setup.${NC}"
fi

# Step 1: Check and configure /boot/config.txt
echo "Step 1: Checking UART5 configuration..."
CONFIG_FILE="/boot/firmware/config.txt"
FALLBACK_CONFIG="/boot/config.txt"

# Determine which config file exists
if [ -f "$CONFIG_FILE" ]; then
    ACTUAL_CONFIG="$CONFIG_FILE"
elif [ -f "$FALLBACK_CONFIG" ]; then
    ACTUAL_CONFIG="$FALLBACK_CONFIG"
else
    echo -e "${RED}Error: Cannot find config.txt in /boot/firmware or /boot${NC}"
    echo "Please locate your config.txt file manually and add: dtoverlay=uart5"
    exit 1
fi

echo "  Using config file: $ACTUAL_CONFIG"

# Check if uart5 is already enabled
if grep -q "dtoverlay=uart5" "$ACTUAL_CONFIG"; then
    echo -e "  ${GREEN}✓ UART5 already enabled${NC}"
    NEEDS_REBOOT=0
else
    echo -e "  ${YELLOW}! UART5 not enabled - adding configuration${NC}"

    # Add uart5 overlay
    if [ -w "$ACTUAL_CONFIG" ]; then
        echo "" >> "$ACTUAL_CONFIG"
        echo "# BNO055 IMU UART5 Configuration" >> "$ACTUAL_CONFIG"
        echo "dtoverlay=uart5" >> "$ACTUAL_CONFIG"
        echo -e "  ${GREEN}✓ Added dtoverlay=uart5 to $ACTUAL_CONFIG${NC}"
        NEEDS_REBOOT=1
    else
        echo -e "  ${RED}! Need sudo to modify $ACTUAL_CONFIG${NC}"
        echo "  Run this command manually:"
        echo "  sudo bash -c 'echo \"dtoverlay=uart5\" >> $ACTUAL_CONFIG'"
        exit 1
    fi
fi

# Step 2: Check Python and packages
echo ""
echo "Step 2: Checking Python environment..."

# Check if Python 3 is installed
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo -e "  ${GREEN}✓ $PYTHON_VERSION found${NC}"
else
    echo -e "  ${RED}✗ Python 3 not found${NC}"
    exit 1
fi

# Check for required Python packages
echo ""
echo "Step 3: Checking required packages..."

check_package() {
    if python3 -c "import $1" 2>/dev/null; then
        echo -e "  ${GREEN}✓ $1 installed${NC}"
        return 0
    else
        echo -e "  ${YELLOW}! $1 not installed${NC}"
        return 1
    fi
}

MISSING_PACKAGES=0

# Check pyserial
if ! check_package "serial"; then
    echo "    Install with: pip3 install pyserial"
    MISSING_PACKAGES=1
fi

# Step 4: Check serial port permissions
echo ""
echo "Step 4: Checking serial port permissions..."

if groups | grep -q 'dialout'; then
    echo -e "  ${GREEN}✓ User is in dialout group${NC}"
else
    echo -e "  ${YELLOW}! User not in dialout group${NC}"
    echo "  Run: sudo usermod -a -G dialout $USER"
    echo "  Then log out and log back in"
fi

# Step 5: Check if UART5 device exists
echo ""
echo "Step 5: Checking UART5 device..."

if [ -e "/dev/ttyAMA5" ]; then
    echo -e "  ${GREEN}✓ /dev/ttyAMA5 exists${NC}"
    ls -l /dev/ttyAMA5
else
    echo -e "  ${YELLOW}! /dev/ttyAMA5 not found${NC}"
    if [ "$NEEDS_REBOOT" -eq 1 ]; then
        echo "  This is expected - reboot required to activate UART5"
    else
        echo "  UART5 overlay is enabled but device not found - try rebooting"
    fi
fi

# Summary
echo ""
echo "=========================================="
echo "  Setup Summary"
echo "=========================================="

if [ "$NEEDS_REBOOT" -eq 1 ]; then
    echo -e "${YELLOW}⚠ REBOOT REQUIRED${NC}"
    echo "  UART5 configuration was added to config.txt"
    echo "  Run: sudo reboot"
    echo ""
fi

if [ "$MISSING_PACKAGES" -eq 1 ]; then
    echo -e "${YELLOW}⚠ MISSING PACKAGES${NC}"
    echo "  Install required packages with:"
    echo "  pip3 install pyserial"
    echo ""
fi

if [ "$NEEDS_REBOOT" -eq 0 ] && [ "$MISSING_PACKAGES" -eq 0 ] && [ -e "/dev/ttyAMA5" ]; then
    echo -e "${GREEN}✓ Setup complete! Ready to test.${NC}"
    echo ""
    echo "Quick test command:"
    echo "  python3 diag.py"
    echo ""
    echo "Run main script:"
    echo "  bash run_claude.sh"
else
    echo "Complete the steps above, then run this script again to verify."
fi

echo ""
echo "Hardware checklist:"
echo "  □ BNO055 connected to GPIO pins 12 & 13 (UART5 TX/RX)"
echo "  □ BNO055 PS1 pin connected to 3.3V (for UART mode)"
echo "  □ BNO055 powered (VIN to 3.3V or 5V, GND to GND)"
echo ""
