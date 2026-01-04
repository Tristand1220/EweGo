# OV5647 overlay (ov5647-i2c1-cam0-overlay)

## What this is
In the Rev 1 version of our carrier, the I2C line on the cam0 interface is connected to a non
standard I2C port on the Raspberry pi.  This directory contains a Device Tree
Overlay that enables an OV5647 camera to be conneted to the (nonstandard) I2C1 bus.

The overlay source `ov5647-i2c1-cam0-overlay.dts`
includes `ov5647.dtsi`, so it **must be run through the C preprocessor** before
compiling with `dtc`.

The overlay is modified from the file, `./arch/arm/boot/dts/overlays/ov5647-overlay.dts`, in 
the kernel source tree (`git clone --depth=1 https://github.com/raspberrypi/linux.git`), and
the include file, `ov5647.dtsi` is copied from there.

---

## Build the overlay (`.dtbo`)

From the directory that contains:
- `ov5647-i2c1-cam0-overlay.dts`
- `ov5647.dtsi`

run:

```bash
# 1) Preprocess (handles the #include of ov5647.dtsi)
cpp -nostdinc -I . -undef -x assembler-with-cpp \
  ov5647-i2c1-cam0-overlay.dts > ov5647-i2c1-cam0-overlay.pp.dts

# 2) Compile to a DT overlay blob
dtc -@ -I dts -O dtb \
  -o ov5647-i2c1-cam0-overlay.dtbo ov5647-i2c1-cam0-overlay.pp.dts
```

Notes:
- `-@` is required for overlays.
- If `cpp` is missing: `sudo apt install build-essential` (or at least `cpp`/`gcc`).

---

## Install the overlay (assumes Trixie - overlays in a different directory for older OS)

```bash
  sudo cp ov5647-i2c1-cam0-overlay.dtbo /boot/firmware/overlays/
```

---

## Enable the overlay in `config.txt`

Edit config:

```bash
sudo nano /boot/firmware/config.txt
# (older systems may use /boot/config.txt)
```

### Now we can replace the cam0 line
```ini
#dtoverlay=ov5647,cam0
dtparam=i2c_arm=on
dtoverlay=ov5647-i2c1-cam0-overlay,cam0_i2c1
```

Reboot after changes:

```bash
sudo reboot
```

---

## Quick verification
After reboot:

```bash
# List detected cameras (libcamera)
libcamera-hello --list-cameras
```

You should see two cameras enumerated if both are connected and enabled.
