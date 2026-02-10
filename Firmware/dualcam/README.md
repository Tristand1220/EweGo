# dual_cam_jp2

## Overview
`dual_cam_jp2.py` is a minimal dual-camera recording program for Raspberry Pi using **Picamera2**.
It captures frames from two CSI cameras simultaneously, writes MJPEG video streams, and logs raw per-frame timestamps to binary files for precise timing analysis.

Each time the program starts, it creates a **new recording session directory**.

---

## Output
On each start, a new directory is created:

```
recordings/YYYYMMDD_HHMMSS/
├── camera1.mjpeg
├── camera1_timestamps.bin
├── camera2.mjpeg
└── camera2_timestamps.bin
```

Timestamp files contain raw little-endian int64 timestamps (nanoseconds).

---

## Running as a systemd service

### Install location
The recommended installation path is:

```
/opt/dualcam/dual_cam_jp2.py
```

Ensure it is executable:

```bash
sudo chmod +x /opt/dualcam/dual_cam_jp2.py
```

---

### Service file
Create the systemd unit:

```bash
sudo nano /etc/systemd/system/dualcam.service
```

Paste:

```ini
[Unit]
Description=Dual Camera Recorder (Picamera2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
NotifyAccess=main
User=pi
Group=pi
WorkingDirectory=/opt/dualcam
ExecStart=/usr/bin/python3 -u /opt/dualcam/dual_cam_jp2.py
KillSignal=SIGTERM
Restart=on-failure
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Adjust `User`/`Group` if you do not use `pi`.

---

### Enable and start
```bash
sudo systemctl daemon-reload
sudo systemctl enable dualcam.service
sudo systemctl start dualcam.service
```

---

## Start / Stop
```bash
sudo systemctl start dualcam.service
sudo systemctl stop dualcam.service
```

Each `start` creates a new recording directory.

---

## Check status
```bash
systemctl status dualcam.service
```

This shows whether the recorder is running and includes a live status line with frame counts and timing statistics.

---

## View logs (recommended)
```bash
journalctl -u dualcam.service -f
```

This shows camera initialization messages and periodic frame timing statistics.

---

## Notes
- The program runs non-interactively when started as a service.
- Stop recording with `systemctl stop`; files are closed cleanly.
- CPU load will be visible in `top` while recording is active.
