# dual_cam_jp2

## Overview
`dual_cam_jp2.py` is a minimal dual-camera recording program for Raspberry Pi using **Picamera2**.
It captures frames from two CSI cameras simultaneously, writes H.264 video streams using the hardware GPU encoder, and logs raw per-frame timestamps to binary files for precise timing analysis.

Each time the program starts, it creates a **new recording session directory**.

---

## Output
On each start, a new directory is created:

```
recordings/YYYYMMDD_HHMMSS/
├── camera1.h264
├── camera1_timestamps.bin
├── camera2.h264
└── camera2_timestamps.bin
```

Timestamp files contain epoch-aligned little-endian int64 timestamps (microseconds from system monotonic clock). Both cameras share the same time base for synchronization.

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
- Uses hardware H.264 encoder at 12 Mbps per camera (minimal CPU load).
- Both cameras run at 1920x1080 @ 24fps (max stutter-free rate on CM4's shared encoder block).

---

## play_with_timestamps.py

Video player for recordings with timestamp-based synchronization.

### Features
- **Auto-detects format**: looks for `.h264` files first, falls back to `.mjpeg` (legacy)
- **Dual camera sync**: builds a frame-pairing map from timestamps so both cameras stay aligned, even with start-time offset or clock drift
- **Seekable playback**: remuxes raw streams to seekable containers (`.mp4` for H.264, `.avi` for MJPEG) via ffmpeg on first run, then caches the result
- **Controls**: SPACE=play/pause, q/ESC=quit, LEFT/a=back 10 frames, RIGHT/d=forward 10 frames, trackbar for seeking

### Usage

```bash
# Dual side-by-side view (default) — run from the camera/ subdir
cd sensor_test_YYYYMMDD_HHMMSS/camera/
uv run python play_with_timestamps.py

# Or point at the session root with --dir (auto-finds camera/ and audio)
uv run python play_with_timestamps.py --dir sensor_test_YYYYMMDD_HHMMSS/

# Single camera
uv run python play_with_timestamps.py 1

# Export dual side-by-side MP4 with audio (auto-detected from session dir)
uv run python play_with_timestamps.py --dir sensor_test_YYYYMMDD_HHMMSS/ --export
uv run python play_with_timestamps.py --dir sensor_test_YYYYMMDD_HHMMSS/ --export output.mp4

# Export without audio
uv run python play_with_timestamps.py --dir sensor_test_YYYYMMDD_HHMMSS/ --export --no-audio

# Convert single camera to MP4
uv run python play_with_timestamps.py 1 --convert --output custom_name.mp4
```

The `--export` path uses ffmpeg natively (no Python frame loop) — fast and lossless remux for the video, re-encoded with libx264 only for the final output. Audio is automatically picked up from `audio_*.wav` in the session directory when `--dir` is used.

### Requirements
- `opencv-python` and `numpy` (`uv run --extra dev` on dev machine)
- `ffmpeg` (installed via apt, dev machine only)
