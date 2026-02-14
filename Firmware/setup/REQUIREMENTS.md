# Firmware Requirements Summary

This document lists all tools, libraries, and system configurations required to run the peripherals in the EweGo project.

*AI GENERATED*

---

## Tools and Libraries by Peripheral

### 1. IMU (BNO055 9-axis IMU)

| Category | Requirement |
|----------|-------------|
| **Hardware** | Bosch BNO055 via UART5 (`/dev/ttyAMA5`), GPIO pins 12/13 |
| **System Config** | `dtoverlay=uart5` in `/boot/firmware/config.txt` |
| **Python Libraries** | `pyserial` |
| **Optional Libraries** | `adafruit-circuitpython-bno055`, `adafruit-blinka` (for baseline test) |
| **System Permissions** | User must be in `dialout` group |
| **Key Scripts** | `poll_imu_data.py`, `visual_imu_display.py`, `check_sensor_connection.py` |

---

### 2. Audio (Stereo Microphone)

| Category | Requirement |
|----------|-------------|
| **Hardware** | MMICT390200012 stereo mics (PDM-to-PCM converted) |
| **System Config** | `dtoverlay=googlevoicehat-soundcard` in `/boot/firmware/config.txt` |
| **System Tools** | `arecord` (ALSA) |
| **Optional Tools** | `ffplay` from FFmpeg (for wireless streaming test) |
| **Key Scripts** | `local_test.sh`, `wireless_test.sh` |

---

### 3. Dual Camera

| Category | Requirement |
|----------|-------------|
| **Hardware** | Two IMX708 CSI cameras (cam0, cam1) |
| **System Config** | `camera_auto_detect=0`<br>`dtoverlay=imx708,cam0`<br>`dtoverlay=imx708,cam1` |
| **Python Libraries** | `picamera2` |
| **System Tools** | `systemd` (for service mode) |
| **Key Scripts** | `dual_cam_jp2.py` |

---

### 4. Fuel Gauge (MAX17048)

| Category | Requirement |
|----------|-------------|
| **Hardware** | MAX17048G+ on I2C bus 3 (`/dev/i2c-3`), address 0x36 |
| **System Config** | `dtoverlay=i2c3,pins_2_3` in `/boot/firmware/config.txt` |
| **Python Libraries** | `smbus2>=0.4.1` |
| **System Tools** | `i2cdetect` (for troubleshooting) |
| **System Permissions** | User must be in `i2c` group |
| **Key Scripts** | `max17048_test.py`, `detect_max17048.py` |

---

### 5. GPS (u-blox ZED-X20P with RTK)

| Category | Requirement |
|----------|-------------|
| **Hardware** | u-blox ZED-X20P via UART4 (`/dev/ttyAMA4`) at 460800 baud |
| **System Config** | `dtoverlay=uart4` in `/boot/firmware/config.txt`<br>Remove `console=serial0,115200` from `/boot/firmware/cmdline.txt` |
| **Python Libraries** | `pyserial`, `pyubx2` |
| **System Permissions** | User must be in `dialout` group |
| **Key Scripts** | `gps_logger.py`, `validate_ubx.py` |

---

## Complete Combined Requirements

### System Configurations (`/boot/firmware/config.txt`)

Add the following lines to `/boot/firmware/config.txt`:

```ini
# Camera configuration
camera_auto_detect=0
dtoverlay=imx708,cam0
dtoverlay=imx708,cam1

# Audio configuration
dtoverlay=googlevoicehat-soundcard

# GPS UART4 configuration
dtoverlay=uart4

# IMU UART5 configuration
dtoverlay=uart5

# Fuel Gauge I2C3 configuration
dtoverlay=i2c3,pins_2_3
dtparam=i2c_arm=on
```

### System Modifications (`/boot/firmware/cmdline.txt`)

**Important:** Remove `console=serial0,115200` (or similar) from `/boot/firmware/cmdline.txt` to prevent GPS UART4 from interfering with boot.

Example of text to find and delete:
```
console=serial0,115200
```

Ensure `cmdline.txt` remains one single line of text without line breaks.

---

### Python Libraries

| Library | Peripherals Used | Install Command |
|---------|------------------|-----------------|
| `pyserial` | IMU, GPS | `pip3 install pyserial` |
| `pyubx2` | GPS | `pip3 install pyubx2` |
| `smbus2>=0.4.1` | Fuel Gauge | `pip3 install smbus2` |
| `picamera2` | Dual Camera | `sudo apt install python3-picamera2` |
| `adafruit-circuitpython-bno055` | IMU (optional) | `pip3 install adafruit-circuitpython-bno055` |
| `adafruit-blinka` | IMU (optional) | `pip3 install adafruit-blinka` |

**One-line install for all Python libraries:**
```bash
pip3 install pyserial pyubx2 smbus2 adafruit-circuitpython-bno055 adafruit-blinka
```

---

### System Tools

| Tool | Peripherals Used | Install Command |
|------|------------------|-----------------|
| `arecord` (ALSA) | Audio | Usually pre-installed on Raspberry Pi OS |
| `ffplay` (FFmpeg) | Audio (optional, for wireless test) | `sudo apt install ffmpeg` |
| `i2cdetect` (i2c-tools) | Fuel Gauge (debug) | `sudo apt install i2c-tools` |
| `systemd` | Dual Camera (service mode) | Pre-installed |

---

### User Group Memberships

Add your user to the required groups for hardware access:

```bash
# For IMU and GPS serial access
sudo usermod -a -G dialout $USER

# For Fuel Gauge I2C access
sudo usermod -a -G i2c $USER
```

**Important:** Log out and log back in (or reboot) for group changes to take effect.

---

## Quick Setup Checklist

1. [ ] Edit `/boot/firmware/config.txt` - add all device tree overlays
2. [ ] Edit `/boot/firmware/cmdline.txt` - remove serial console
3. [ ] Reboot the Raspberry Pi
4. [ ] Install Python libraries: `pip3 install pyserial pyubx2 smbus2`
5. [ ] Install picamera2: `sudo apt install python3-picamera2`
6. [ ] Add user to groups: `dialout` and `i2c`
7. [ ] Log out and back in (or reboot)
8. [ ] Test each peripheral with its respective script

---

## Hardware Summary

| Peripheral | Interface | Device Path | I2C Address | Baud Rate |
|------------|-----------|-------------|-------------|-----------|
| IMU (BNO055) | UART5 | `/dev/ttyAMA5` | N/A | 115200 |
| GPS (ZED-X20P) | UART4 | `/dev/ttyAMA4` | N/A | 460800 |
| Fuel Gauge (MAX17048) | I2C3 | `/dev/i2c-3` | 0x36 | N/A |
| Audio | ALSA | `hw:2,0` | N/A | N/A |
| Camera 1 | CSI | `/dev/video0` | N/A | N/A |
| Camera 2 | CSI | `/dev/video1` | N/A | N/A |
