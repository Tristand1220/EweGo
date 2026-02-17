# BNO055 IMU Quick Start Guide

## Initial Setup (One-Time)

```bash
# Run the setup script
./setup_imu.sh

# If it prompts for reboot, reboot the Pi
sudo reboot
```

## Important Files Summary

### 🚀 Main Scripts
- **`poll_imu_data.py`** - Main IMU polling script (custom implementation)
  - Reads Euler angles, linear acceleration, gravity, quaternions
  - 10 Hz polling rate
  - Clean terminal output
  - Run with: `bash run_imu_polling.sh`

- **`visual_imu_display.py`** - Visual display version (recommended)
  - Terminal-based visualization
  - Real-time updating display
  - Shows heading compass, orientations, vectors
  - Run with: `python3 visual_imu_display.py`

### 🧪 Testing Scripts
- **`check_sensor_connection.py`** - Hardware diagnostic tool
  - Quick test to verify IMU is responding
  - Reads chip ID register
  - Use this first to test connectivity
  - Run with: `python3 check_sensor_connection.py`

- **`test_adafruit_baseline.py`** - Adafruit library test
  - Uses official Adafruit CircuitPython library
  - Good baseline test
  - Shows raw TX/RX bytes for debugging
  - Requires: `pip install adafruit-circuitpython-bno055 pyserial adafruit-blinka`
  - Run with: `python3 test_adafruit_baseline.py`

### 🔧 Configuration
- **`/boot/firmware/config.txt`** (or `/boot/config.txt`)
  - Must contain: `dtoverlay=uart5`
  - Enables UART5 on /dev/ttyAMA5

### 📝 Documentation
- **`DEBUG_PROGRESS.md`** - Detailed debugging notes and fixes
- **`QUICK_START.md`** - This file

## Hardware Connection

```
BNO055 Pin    →  Raspberry Pi
---------------------------------
VIN           →  3.3V or 5V
GND           →  GND
SDA (UART RX) →  GPIO 12 (UART5 TX)
SCL (UART TX) →  GPIO 13 (UART5 RX)
PS1           →  3.3V (CRITICAL - enables UART mode)
```

## Quick Test Workflow

1. **Hardware Test**
   ```bash
   python3 check_sensor_connection.py
   ```
   Expected: "SUCCESS: Sensor is alive and talking!"

2. **Basic Data Test**
   ```bash
   python3 test_adafruit_baseline.py
   ```
   Expected: Table showing temperature, euler angles, calibration status
   Note: Requires Adafruit libraries installation

3. **Main Script**
   ```bash
   bash run_imu_polling.sh
   ```
   Expected: Polling data at 10 Hz with orientation and acceleration

4. **Visual Display (Recommended)**
   ```bash
   python3 visual_imu_display.py
   ```
   Expected: Real-time visual representation in terminal

## Common Issues

### "Permission denied" on /dev/ttyAMA5
```bash
sudo usermod -a -G dialout $USER
# Log out and log back in
```

### "/dev/ttyAMA5 not found"
- Check that `dtoverlay=uart5` is in config.txt
- Reboot after adding it
- Verify with: `ls /dev/ttyAMA*`

### "No response from sensor"
- Verify PS1 pin is connected to 3.3V
- Check TX/RX wiring (TX → RX, RX → TX)
- Power cycle the sensor
- Run `python3 check_sensor_connection.py` to test

### "Write error: 0x01" or sensor hung
- Power cycle the BNO055 sensor (unplug and replug VIN)
- The sensor can get stuck and needs hardware reset

## File Organization

```
IMU_testing/
├── setup_imu.sh                  ← Run this first on new device
├── QUICK_START.md                ← This guide
├── poll_imu_data.py              ← Main polling script
├── visual_imu_display.py         ← Visual display (recommended)
├── check_sensor_connection.py    ← Quick hardware test
├── test_adafruit_baseline.py     ← Adafruit library test
├── run_imu_polling.sh            ← Launcher for polling script
└── DEBUG_PROGRESS.md             ← Detailed debugging notes
```

## Configuration Details

- **Serial Port**: /dev/ttyAMA5
- **Baud Rate**: 115200
- **Operation Mode**: NDOF (9-axis fusion)
- **Expected Chip ID**: 0xA0
- **Poll Rate**: 10 Hz (configurable in scripts)

## Next Steps After Setup

1. Run `diag.py` to verify hardware
2. Calibrate the sensor by moving it through all axes
3. Use `claude.py` for continuous monitoring
4. Use `claude_visual.py` for visual feedback
