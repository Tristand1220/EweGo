# BNO055 IMU Testing Suite

Quick setup and testing system for BNO055 IMU sensor on Raspberry Pi.

## Setup (30 seconds)

```bash
# 1. Run setup script
./setup_imu.sh

# 2. Reboot if prompted
sudo reboot

# 3. Test hardware
python3 check_sensor_connection.py
```

## Test Commands

```bash
# Quick hardware test
python3 check_sensor_connection.py

# Full data test (Adafruit library - requires installation)
python3 test_adafruit_baseline.py

# Main polling script (10 Hz)
bash run_imu_polling.sh

# Visual display (recommended)
python3 visual_imu_display.py
```

## Key Files

| File | Purpose |
|------|---------|
| `setup_imu.sh` | One-time setup for new devices |
| `check_sensor_connection.py` | Quick hardware connectivity test |
| `test_adafruit_baseline.py` | Baseline test using Adafruit library |
| `poll_imu_data.py` | Main IMU data polling script |
| `visual_imu_display.py` | Terminal-based visual display (recommended) |
| `run_imu_polling.sh` | Launcher script for polling |
| `QUICK_START.md` | Detailed guide and troubleshooting |

## Hardware Requirements

- BNO055 IMU sensor
- Raspberry Pi with UART5 (GPIO 12/13)
- **PS1 pin must be connected to 3.3V** (enables UART mode)

See `QUICK_START.md` for full details, wiring diagram, and troubleshooting.
