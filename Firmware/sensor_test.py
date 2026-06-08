#!/usr/bin/env python3
"""
Sensor Test - Unified Data Logger
Runs all sensors in parallel for battery life testing:
- IMU (BNO055) - 50Hz logging
- Audio Recorder - Continuous recording
- Dual Camera - H.264 30fps recording
- Fuel Gauge (MAX17048) - Battery monitoring every 2 seconds

Press Ctrl+C to stop all processes gracefully
"""

import subprocess
import signal
import sys
import time
import os
import csv
from pathlib import Path
from datetime import datetime
import threading

# Resolve paths relative to this script's location (Firmware/)
FIRMWARE_DIR = Path(__file__).resolve().parent


class BatteryLifeTest:
    """Orchestrates all sensor processes for battery life testing"""

    def __init__(self):
        self.processes = {}
        self.running = False
        self.session = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Put output in the project root (one level above Firmware/)
        self.log_dir = FIRMWARE_DIR.parent / f"sensor_test_{self.session}"
        self.log_dir.mkdir(exist_ok=True)

        # Create subdirectories for organized logging
        (self.log_dir / "imu").mkdir(exist_ok=True)
        (self.log_dir / "camera").mkdir(exist_ok=True)
        (self.log_dir / "gps").mkdir(exist_ok=True)

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print("\n\n[SIGNAL] Shutdown requested...")
        self.stop_all()
        sys.exit(0)

    def _clock_sync_worker(self, out_path):
        """Write (monotonic_us, wall_time_s) pairs at 10 ms intervals.

        monotonic_us: time.monotonic_ns() // 1000 — unaffected by NTP, same
          clock domain as camera timestamps and all other sensor timestamps.
        wall_time_s:  time.time() — chrony-disciplined wall clock.

        Together these let post-processing convert any monotonic timestamp to
        wall time and correlate events across devices via chrony-synced wall time.
        """
        interval_s = 0.01  # 10 ms = 100 Hz
        with open(out_path, "w", newline="", buffering=1) as f:
            writer = csv.writer(f)
            writer.writerow(["monotonic_us", "wall_time_s"])
            next_tick = time.monotonic()
            while self.running:
                mono = time.monotonic_ns() // 1000
                wall = time.time()
                writer.writerow([mono, f"{wall:.6f}"])
                next_tick += interval_s
                sleep_s = next_tick - time.monotonic()
                if sleep_s > 0:
                    time.sleep(sleep_s)

    def start_imu_logger(self, max_retries=3):
        """Start IMU data logger with retries (backup to IMU's internal retry)"""
        print("Starting IMU logger...")

        imu_script = FIRMWARE_DIR / "IMU" / "log_imu_data.py"
        imu_log_dir = self.log_dir / "imu"
        cmd = [
            sys.executable,
            str(imu_script),
            "--port", "/dev/ttyAMA5",
            "--rate", "50"
        ]

        def kill_stale_port_users():
            """Kill any process holding the IMU serial port from a previous run."""
            try:
                result = subprocess.run(
                    ["fuser", "/dev/ttyAMA5"],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    pids = result.stdout.strip().split()
                    for pid in pids:
                        pid = pid.strip()
                        if pid.isdigit():
                            print(f"  Killing stale process {pid} on /dev/ttyAMA5")
                            os.kill(int(pid), signal.SIGKILL)
                    time.sleep(0.5)
            except Exception:
                pass

        def launch():
            kill_stale_port_users()
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(imu_log_dir)
            )
            self.processes['imu'] = proc
            print(f"  PID: {proc.pid}")
            print(f"  Logging to: {imu_log_dir}")
            return proc

        proc = launch()

        def monitor():
            nonlocal proc
            for attempt in range(max_retries):
                for line in proc.stdout:
                    line = line.rstrip()
                    if line.startswith("[") and "] H:" in line:
                        continue
                    if any(kw in line for kw in [
                        "============", "BNO055", "initialized", "Logging to:",
                        "WARNING", "Failed", "Error", "Shutting down", "Logged",
                        "samples to", "Init failed", "retrying", "attempt", "Traceback",
                        "Exception", "Permission"
                    ]):
                        print(f"[IMU] {line}")

                # Process ended — check if it crashed
                rc = proc.wait()
                if rc == 0 or not self.running:
                    return
                retries_left = max_retries - attempt - 1
                if retries_left > 0:
                    # Kill any zombie that might still hold the serial port
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                    print(f"[IMU] Crashed (exit {rc}), retrying in 3s ({retries_left} left)...")
                    time.sleep(3)
                    proc = launch()
                else:
                    print(f"[IMU] Crashed (exit {rc}), no retries left")

        threading.Thread(target=monitor, daemon=True).start()

    def start_audio_recorder(self):
        """Start audio recorder"""
        print("Starting audio recorder...")
        audio_file = self.log_dir / f"audio_{self.session}.wav"
        audio_script = FIRMWARE_DIR / "audio" / "record_audio.py"
        cmd = [
            sys.executable,
            str(audio_script),
            "--output", str(audio_file)
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        self.processes['audio'] = proc
        print(f"  PID: {proc.pid}")
        print(f"  Recording to: {audio_file}")

        def monitor():
            for line in proc.stdout:
                line = line.rstrip()
                if any(kw in line for kw in [
                    "Recording to:", "Recording stopped", "Recording saved",
                    "Error", "WARNING", "Failed", "Aborted"
                ]):
                    print(f"[AUDIO] {line}")
        threading.Thread(target=monitor, daemon=True).start()

    def start_camera_recorder(self):
        """Start dual camera recorder"""
        print("Starting dual camera recorder...")

        camera_output_dir = self.log_dir / "camera"
        cam_script = FIRMWARE_DIR / "dualcam" / "dual_cam_jp2.py"
        cmd = [
            sys.executable,
            "-c",
            f"""
import sys
sys.path.insert(0, '{FIRMWARE_DIR / "dualcam"}')

import dual_cam_jp2
from pathlib import Path

# Override the MinimalRecorder __init__ to use our output directory
original_init = dual_cam_jp2.MinimalRecorder.__init__

def patched_init(self):
    self.cam1 = None
    self.cam2 = None
    self.out1 = None
    self.out2 = None
    self.running = False
    self.session = "{self.session}"
    self.dir = Path("{camera_output_dir}")
    self.dir.mkdir(parents=True, exist_ok=True)

dual_cam_jp2.MinimalRecorder.__init__ = patched_init

dual_cam_jp2.main()
"""
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        self.processes['camera'] = proc
        print(f"  PID: {proc.pid}")
        print(f"  Recording to: {camera_output_dir}")

        def monitor():
            for line in proc.stdout:
                line = line.rstrip()
                if "INFO" in line or "CAM1:" in line or "CAM2:" in line:
                    continue
                if line.strip() and not line.startswith("["):
                    print(f"[CAMERA] {line}")
        threading.Thread(target=monitor, daemon=True).start()

    def start_fuel_gauge_logger(self):
        """Start fuel gauge logger"""
        print("Starting fuel gauge logger...")

        fuel_log_file = self.log_dir / f"fuel_gauge_{self.session}.csv"
        cmd = [
            sys.executable,
            "-c",
            f"""
import sys
sys.path.insert(0, '{FIRMWARE_DIR / "fuel_gauge"}')
from max17048_test import MAX17048, run_continuous_monitoring

fg = MAX17048(bus_number=1)
print("Fuel gauge initialized")

try:
    run_continuous_monitoring(fg, duration=315360000, interval=2, log_file='{fuel_log_file}')
finally:
    fg.close()
"""
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        self.processes['fuel_gauge'] = proc
        print(f"  PID: {proc.pid}")
        print(f"  Logging to: {fuel_log_file}")

        def monitor():
            for line in proc.stdout:
                line = line.rstrip()
                if any(kw in line for kw in [
                    "Connected to", "initialized", "Logging to:",
                    "Monitoring stopped", "Completed", "readings",
                    "WARNING", "Failed"
                ]):
                    print(f"[FUEL] {line}")
        threading.Thread(target=monitor, daemon=True).start()

    def start_gps_logger(self):
        """Start GPS logger"""
        print("Starting GPS logger...")

        gps_log_dir = self.log_dir / "gps"
        gps_script = FIRMWARE_DIR / "gps-test" / "gps_logger.py"
        cmd = [
            sys.executable,
            str(gps_script),
            "--port", "/dev/ttyAMA4",
            "--baud", "460800",
            "--log-dir", str(gps_log_dir),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        self.processes['gps'] = proc
        print(f"  PID: {proc.pid}")
        print(f"  Logging to: {gps_log_dir}")

        def monitor():
            for line in proc.stdout:
                line = line.rstrip()
                if any(kw in line for kw in [
                    "Connected", "Logging", "Fix:", "NTRIP", "error",
                    "failed", "stopped", "Statistics"
                ]):
                    print(f"[GPS] {line}")
        threading.Thread(target=monitor, daemon=True).start()

    def start_all(self, enable_gps=True):
        """Start all processes"""
        print("=" * 70)
        print("SENSOR TEST - UNIFIED DATA LOGGER")
        print("=" * 70)
        print(f"Session: {self.session}")
        print(f"Log directory: {self.log_dir}")
        print("=" * 70)
        print()

        self.running = True

        # Start clock-sync logger before any sensor so the log spans the
        # full session including sensor startup.
        threading.Thread(
            target=self._clock_sync_worker,
            args=(self.log_dir / "clock_sync.csv",),
            daemon=True,
        ).start()

        try:
            # Start cameras first — they take longest to initialize and
            # their init interferes with the IMU's UART.
            self.start_camera_recorder()
            time.sleep(3)  # Wait for camera init to finish

            self.start_audio_recorder()
            time.sleep(1)

            self.start_imu_logger()
            time.sleep(1)

            self.start_fuel_gauge_logger()
            time.sleep(1)

            if enable_gps:
                self.start_gps_logger()
                time.sleep(1)

            print()
            print("=" * 70)
            print("ALL PROCESSES STARTED SUCCESSFULLY")
            print("=" * 70)
            print("Press Ctrl+C to stop all processes")
            print("=" * 70)
            print()

            self._monitor_processes()

        except Exception as e:
            print(f"\n  Error starting processes: {e}")
            import traceback
            traceback.print_exc()
            self.stop_all()
            return 1

        return 0

    def _monitor_processes(self):
        """Monitor all processes and report if any crash"""
        while self.running:
            time.sleep(5)

            for name, proc in list(self.processes.items()):
                if proc.poll() is not None:
                    print(f"\n  WARNING: {name.upper()} process died (exit code: {proc.returncode})")

    def stop_all(self):
        """Stop all processes gracefully"""
        if not self.running:
            return

        print("\n" + "=" * 70)
        print("STOPPING ALL PROCESSES...")
        print("=" * 70)

        self.running = False

        for name, proc in self.processes.items():
            if proc.poll() is None:
                print(f"Stopping {name}...")
                try:
                    proc.send_signal(signal.SIGINT)
                except Exception as e:
                    print(f"  Error sending signal to {name}: {e}")

        print("\nWaiting for processes to exit gracefully...")
        for name, proc in self.processes.items():
            try:
                proc.wait(timeout=10)
                print(f"  {name} stopped")
            except subprocess.TimeoutExpired:
                print(f"  {name} did not stop gracefully, forcing...")
                proc.kill()
                proc.wait()

        print("\n" + "=" * 70)
        print("ALL PROCESSES STOPPED")
        print("=" * 70)
        print(f"\nAll data saved to: {self.log_dir}/")
        print("\nSummary:")
        print("  - Clock sync: " + str(self.log_dir / "clock_sync.csv"))
        print("  - IMU logs: " + str(self.log_dir / "imu/"))
        print("  - Audio: " + str(self.log_dir / f"audio_{self.session}.wav"))
        print("  - Camera: " + str(self.log_dir / "camera/"))
        print("  - Fuel gauge: " + str(self.log_dir / f"fuel_gauge_{self.session}.csv"))
        if 'gps' in self.processes:
            print("  - GPS: " + str(self.log_dir / "gps/"))
        print("=" * 70)


def main():
    """Main entry point"""
    import argparse
    parser = argparse.ArgumentParser(description="EweGo unified sensor logger")
    parser.add_argument('--no-gps', action='store_true', help='Disable GPS logger (if GPS not installed)')
    args = parser.parse_args()

    test = BatteryLifeTest()

    try:
        return test.start_all(enable_gps=not args.no_gps)
    except KeyboardInterrupt:
        print("\n\nInterrupt received...")
        test.stop_all()
        return 0
    except Exception as e:
        print(f"\n  Fatal error: {e}")
        import traceback
        traceback.print_exc()
        test.stop_all()
        return 1


if __name__ == "__main__":
    sys.exit(main())
