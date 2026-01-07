#!/usr/bin/env python3
"""
Minimal dual camera recorder with RAW timestamp logging to disk
Writes timestamps as raw float64 binary data directly to disk
"""

import time
import signal
import sys
import struct
import threading
from pathlib import Path
from datetime import datetime
import shutil
import subprocess
import os


# --- systemd status integration -------------------------------------------
# If this program is launched by systemd with Type=notify, we can expose a
# compact runtime status line in `systemctl status <service>`.
# This is a no-op when not running under systemd (no NOTIFY_SOCKET) or when
# systemd-notify is not available.
_SYSTEMD_NOTIFY = shutil.which("systemd-notify")


def _systemd_notify(msg: str) -> None:
    if not _SYSTEMD_NOTIFY:
        return
    if "NOTIFY_SOCKET" not in os.environ:
        return
    try:
        subprocess.run([_SYSTEMD_NOTIFY, msg], check=False)
    except Exception:
        # Never allow status updates to affect recording.
        pass


def systemd_ready() -> None:
    _systemd_notify("READY=1")


def systemd_set_status(status: str) -> None:
    _systemd_notify(f"STATUS={status}")


# --- Camera / Recording code ------------------------------------------------

try:
    from picamera2 import Picamera2
    # from picamera2.encoders import H264Encoder
    # from picamera2.outputs import FileOutput
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
except ImportError:
    print("picamera2 not installed. Install with: sudo apt install python3-picamera2")
    sys.exit(1)


class RawTimestampOutput(FileOutput):
    """
    FileOutput subclass that writes raw int64 timestamps to a separate .bin file
    and tracks inter-frame intervals for stats.
    """

    def __init__(self, video_file, timestamp_file, camera_id):
        super().__init__(video_file)
        self.camera_id = camera_id
        self.ts_file = open(timestamp_file, "wb")  # Binary write
        self.last_ts = None
        self.count = 0

        # Stats tracking
        self.intervals = []
        self.interval_sum = 0
        self.interval_min = float("inf")
        self.interval_max = 0

    def outputframe(self, frame, keyframe=True, timestamp=None, packet=None, audio=None):
        # Convert to seconds (float64) for interval computation
        ts = timestamp / 1e6 if timestamp else 0.0

        # Write raw timestamp to disk immediately (8 bytes, little-endian int64)
        self.ts_file.write(struct.pack("<q", timestamp))
        self.count += 1

        # Calculate interval for stats
        if self.last_ts is not None:
            interval = (ts - self.last_ts) * 1000  # ms
            self.intervals.append(interval)
            self.interval_sum += interval
            if interval < self.interval_min:
                self.interval_min = interval
            if interval > self.interval_max:
                self.interval_max = interval

        self.last_ts = ts

        # Write frame to video output
        return super().outputframe(frame, keyframe, timestamp, packet, audio)

    def get_stats(self):
        """Get current statistics and reset tracking"""
        if not self.intervals:
            return None

        stats = {
            "count": self.count,
            "avg": self.interval_sum / len(self.intervals),
            "min": self.interval_min,
            "max": self.interval_max,
        }

        # Reset interval tracking (keep count)
        self.intervals = []
        self.interval_sum = 0
        self.interval_min = float("inf")
        self.interval_max = 0

        return stats

    def close(self):
        """Close timestamp file"""
        self.ts_file.close()
        return super().close()


class MinimalRecorder:
    def __init__(self):
        self.cam1 = None
        self.cam2 = None
        self.out1 = None
        self.out2 = None
        self.running = False

        # Output directory (new session per start)
        self.session = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.dir = Path(f"recordings/{self.session}")
        self.dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        """Initialize and start recording"""
        print("Initializing cameras...")

        # Camera 1
        self.cam1 = Picamera2(0)
        config1 = self.cam1.create_video_configuration(
            main={"size": (1920, 1080), "format": "RGB888"},
            controls={"FrameRate": 30},
            buffer_count=16,
        )
        self.cam1.configure(config1)

        # Camera 2
        self.cam2 = Picamera2(1)
        config2 = self.cam2.create_video_configuration(
            main={"size": (1920, 1080), "format": "RGB888"},
            controls={"FrameRate": 30},
            buffer_count=16,
        )
        self.cam2.configure(config2)

        print("Starting recording...")

        # Create outputs with raw timestamp files
        self.out1 = RawTimestampOutput(
            str(self.dir / "camera1.mjpeg"),
            str(self.dir / "camera1_timestamps.bin"),
            camera_id=1,
        )
        self.out2 = RawTimestampOutput(
            str(self.dir / "camera2.mjpeg"),
            str(self.dir / "camera2_timestamps.bin"),
            camera_id=2,
        )

        # Create encoders
        enc1 = JpegEncoder()
        enc2 = JpegEncoder()

        # Start cameras
        self.cam1.start()
        time.sleep(0.1)
        self.cam2.start()
        time.sleep(0.1)

        # Start recording
        self.cam1.start_recording(enc1, self.out1)
        self.cam2.start_recording(enc2, self.out2)

        self.running = True
        print(f"Recording to: {self.dir}")
        print("Timestamps: camera1_timestamps.bin, camera2_timestamps.bin")
        print("Format: Raw binary float64 (8 bytes per timestamp)")
        print("Expected interval: 33.33ms @ 30fps\n")

        # Update systemd service status (optional; requires Type=notify)
        systemd_set_status(f"Recording to {self.dir}")
        systemd_ready()

        # Start stats thread
        threading.Thread(target=self.print_stats, daemon=True).start()

    def print_stats(self):
        """Print statistics every period and update systemd status if present."""
        while self.running:
            time.sleep(10)

            stats1 = self.out1.get_stats() if self.out1 else None
            stats2 = self.out2.get_stats() if self.out2 else None

            if stats1:
                # Camera 1
                print(
                    f"CAM1: {stats1['count']:4d}f | "
                    f"avg={stats1['avg']:5.1f}ms | "
                    f"min={stats1['min']:5.1f}ms | "
                    f"max={stats1['max']:6.1f}ms"
                )

            if stats2:
                # Camera 2
                print(
                    f"CAM2: {stats2['count']:4d}f | "
                    f"avg={stats2['avg']:5.1f}ms | "
                    f"min={stats2['min']:5.1f}ms | "
                    f"max={stats2['max']:6.1f}ms"
                )
                print()

            # Update systemd status line (compact summary)
            parts = []
            if stats1:
                parts.append(
                    f"cam1 frames={stats1['count']} avg={stats1['avg']:.1f}ms max={stats1['max']:.1f}ms"
                )
            if stats2:
                parts.append(
                    f"cam2 frames={stats2['count']} avg={stats2['avg']:.1f}ms max={stats2['max']:.1f}ms"
                )
            if parts:
                systemd_set_status(" | ".join(parts))

    def stop(self):
        """Stop recording"""
        if not self.running:
            return

        print("\nStopping...")
        systemd_set_status("Stopping")
        self.running = False

        # Stop recording
        try:
            if self.cam1 and self.out1:
                self.cam1.stop_recording()
        except Exception:
            pass
        try:
            if self.cam2 and self.out2:
                self.cam2.stop_recording()
        except Exception:
            pass

        # Stop cameras
        try:
            if self.cam1:
                self.cam1.stop()
        except Exception:
            pass
        try:
            if self.cam2:
                self.cam2.stop()
        except Exception:
            pass

        # Close outputs (closes timestamp file too)
        try:
            if self.out1:
                self.out1.close()
        except Exception:
            pass
        try:
            if self.out2:
                self.out2.close()
        except Exception:
            pass

        print("Stopped.")
        systemd_set_status("Stopped")


def main():
    recorder = MinimalRecorder()

    def signal_handler(sig, frame):
        print("\n\nInterrupt received...")
        recorder.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start recording
    try:
        recorder.start()

        # If running interactively, allow 'q' to stop.
        # If running under systemd (no TTY), just run until SIGTERM/SIGINT.
        if sys.stdin.isatty():
            print("Press 'q' + Enter to stop, or Ctrl+C")
            while True:
                try:
                    cmd = input()
                    if cmd.lower() == "q":
                        break
                except EOFError:
                    break
            recorder.stop()
        else:
            print("No TTY detected (service mode). Running until SIGTERM.")
            while recorder.running:
                time.sleep(1)

        recorder.stop()

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        recorder.stop()


if __name__ == "__main__":
    main()

