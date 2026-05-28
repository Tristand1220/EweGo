#!/usr/bin/env python3
"""
analyze_sync.py — Camera synchronization analyzer for dual_cam_jp2_hw.py sessions.

Reads a recording session directory produced by dual_cam_jp2_hw.py and an optional
sine_params.json produced by sine_display.py, then generates three diagnostic plots:

  Plot 1 — Brightness vs. UTC time  (both cameras + ground-truth sine)
  Plot 2 — Inter-camera timestamp delta  (cam1_ts - nearest cam2_ts, in ms)
  Plot 3 — Frame interval jitter per camera

Usage:
    python3 analyze_sync.py SESSION_DIR [OPTIONS]

    SESSION_DIR     Path to recordings/YYYYMMDD_HHMMSS/ produced by the recorder.

Options:
    --sine-params PATH   Path to sine_params.json  (default: SESSION_DIR/sine_params.json)
    --roi X Y W H        Region of interest in pixels for brightness sampling
                         (default: centre 100×100 of the frame)
    --cam1-res W H       Camera 1 resolution  (default: 2304 1296)
    --cam2-res W H       Camera 2 resolution  (default: 1920 1080)
    --max-frames INT     Cap frames to read per camera  (0 = all)
    --output PATH        Save figure to file instead of showing interactively
    --verbose            Print per-frame data to stdout
"""

import argparse
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:
    print("opencv-python not installed.  pip install opencv-python")
    sys.exit(1)

try:
    import matplotlib
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.ticker import AutoMinorLocator
except ImportError:
    print("matplotlib not installed.  pip install matplotlib")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Matplotlib style — clean dark theme suited to a terminal/lab environment
# ---------------------------------------------------------------------------

plt.rcParams.update({
    "figure.facecolor":  "#0d0d0d",
    "axes.facecolor":    "#141414",
    "axes.edgecolor":    "#333333",
    "axes.labelcolor":   "#cccccc",
    "axes.titlecolor":   "#eeeeee",
    "axes.grid":         True,
    "grid.color":        "#2a2a2a",
    "grid.linewidth":    0.8,
    "xtick.color":       "#888888",
    "ytick.color":       "#888888",
    "text.color":        "#cccccc",
    "legend.facecolor":  "#1a1a1a",
    "legend.edgecolor":  "#333333",
    "legend.labelcolor": "#cccccc",
    "lines.linewidth":   1.4,
    "font.family":       "monospace",
})

CAM1_COLOR   = "#4fc3f7"   # cool blue
CAM2_COLOR   = "#ffb74d"   # warm amber
TRUTH_COLOR  = "#aaaaaa"   # neutral gray (dashed)
DELTA_COLOR  = "#ef5350"   # red
JITTER1_COLOR = CAM1_COLOR
JITTER2_COLOR = CAM2_COLOR


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("session_dir", type=Path,
                   help="Recording session directory")
    p.add_argument("--sine-params", type=Path, default=None,
                   help="Path to sine_params.json")
    p.add_argument("--roi", type=int, nargs=4, default=None,
                   metavar=("X", "Y", "W", "H"),
                   help="ROI for brightness sampling (pixels)")
    p.add_argument("--cam1-res", type=int, nargs=2, default=[2304, 1296],
                   metavar=("W", "H"))
    p.add_argument("--cam2-res", type=int, nargs=2, default=[1920, 1080],
                   metavar=("W", "H"))
    p.add_argument("--max-frames", type=int, default=0,
                   help="Max frames to decode per camera (0 = all)")
    p.add_argument("--output", type=Path, default=None,
                   help="Save plot to this file path instead of displaying")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Timestamp file helpers
# ---------------------------------------------------------------------------

def load_timestamps_bin(path: Path) -> np.ndarray:
    """
    Read a raw binary timestamp file written by RawTimestampOutput.
    Each entry is a little-endian int64 (microseconds, monotonic clock).
    Returns a 1-D int64 numpy array.
    """
    data = path.read_bytes()
    n    = len(data) // 8
    if n == 0:
        raise ValueError(f"Timestamp file is empty: {path}")
    timestamps = np.frombuffer(data[:n * 8], dtype="<i8").copy()
    return timestamps


def load_anchor(session_dir: Path):
    """
    Returns (start_utc_seconds: float, start_mono_us: int).
    start_utc_seconds is Unix epoch seconds (float).
    """
    from datetime import datetime, timezone

    wall_str = (session_dir / "start_time.txt").read_text().strip()
    mono_str = (session_dir / "start_time_mono_us.txt").read_text().strip()

    # parse ISO format (may include +00:00 suffix)
    try:
        dt = datetime.fromisoformat(wall_str)
    except ValueError:
        # fallback — strip timezone suffix and assume UTC
        dt = datetime.fromisoformat(wall_str.replace("Z", "+00:00"))

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.timestamp(), int(mono_str)


def mono_us_to_utc(mono_us_arr: np.ndarray,
                   start_utc_s: float,
                   start_mono_us: int) -> np.ndarray:
    """
    Convert an array of monotonic-clock microsecond timestamps to
    UTC seconds (float64).

    UTC(t) = start_utc_s + (mono_us(t) - start_mono_us) / 1e6
    """
    return start_utc_s + (mono_us_arr - start_mono_us) / 1e6


# ---------------------------------------------------------------------------
# MJPEG frame extraction
# ---------------------------------------------------------------------------

def extract_brightness(mjpeg_path: Path,
                       roi: tuple,
                       max_frames: int = 0,
                       verbose: bool = False) -> np.ndarray:
    """
    Decode an MJPEG file with OpenCV and return mean brightness of the ROI
    for each frame as a float32 array.

    roi: (x, y, w, h) in pixels  — if None, use the centre 100×100
    """
    cap = cv2.VideoCapture(str(mjpeg_path))
    if not cap.isOpened():
        raise IOError(f"Cannot open video file: {mjpeg_path}")

    brightness_values = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Determine ROI on first frame if not supplied
        if roi is None:
            h, w = frame.shape[:2]
            cx, cy = w // 2, h // 2
            roi = (cx - 50, cy - 50, 100, 100)

        x, y, rw, rh = roi
        # Clamp to frame bounds
        fh, fw = frame.shape[:2]
        x  = max(0, min(x,  fw - 1))
        y  = max(0, min(y,  fh - 1))
        rw = max(1, min(rw, fw - x))
        rh = max(1, min(rh, fh - y))

        patch = frame[y:y+rh, x:x+rw]
        gray  = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if len(patch.shape) == 3 else patch
        mean  = float(np.mean(gray))
        brightness_values.append(mean)

        if verbose:
            print(f"  frame {frame_idx:5d}  brightness={mean:6.2f}")

        frame_idx += 1
        if max_frames > 0 and frame_idx >= max_frames:
            break

    cap.release()
    return np.array(brightness_values, dtype=np.float32)


# ---------------------------------------------------------------------------
# Ground-truth sine reconstruction
# ---------------------------------------------------------------------------

def ground_truth_sine(utc_times: np.ndarray, params: dict) -> np.ndarray:
    """
    Reconstruct B(t) for an array of UTC epoch seconds using sine_params.json.
    B(t) = offset + amplitude * sin(2π·freq·(t - t0) + phase)
    """
    from datetime import datetime, timezone
    t0_str = params["start_utc"]
    try:
        t0_dt = datetime.fromisoformat(t0_str)
    except ValueError:
        t0_dt = datetime.fromisoformat(t0_str.replace("Z", "+00:00"))
    if t0_dt.tzinfo is None:
        t0_dt = t0_dt.replace(tzinfo=timezone.utc)
    t0 = t0_dt.timestamp()

    elapsed   = utc_times - t0
    freq      = params["freq_hz"]
    amplitude = params["amplitude"]
    offset    = params["offset"]
    phase     = params.get("phase_rad", 0.0)

    return offset + amplitude * np.sin(2 * math.pi * freq * elapsed + phase)


# ---------------------------------------------------------------------------
# Nearest-neighbour matching for inter-camera delta
# ---------------------------------------------------------------------------

def nearest_delta_ms(ts_a: np.ndarray, ts_b: np.ndarray) -> np.ndarray:
    """
    For each timestamp in ts_a (UTC seconds), find the nearest timestamp in ts_b
    and return (ts_a[i] - ts_b[nearest]) in milliseconds.
    Uses a simple searchsorted approach — O(N log N).
    """
    idx   = np.searchsorted(ts_b, ts_a, side="left")
    idx   = np.clip(idx, 0, len(ts_b) - 1)

    # Also check idx-1 to find true nearest
    idx_m = np.clip(idx - 1, 0, len(ts_b) - 1)
    diff0 = np.abs(ts_a - ts_b[idx])
    diffm = np.abs(ts_a - ts_b[idx_m])
    nearest_ts = np.where(diffm < diff0, ts_b[idx_m], ts_b[idx])

    return (ts_a - nearest_ts) * 1000.0   # → ms


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def make_figure(
    utc1: np.ndarray, bright1: np.ndarray,
    utc2: np.ndarray, bright2: np.ndarray,
    sine_params: dict | None,
    session_label: str,
):
    fig = plt.figure(figsize=(16, 11))
    fig.suptitle(
        f"Camera Sync Analysis  ·  Session: {session_label}",
        fontsize=13, fontweight="bold", y=0.98,
        color="#eeeeee",
    )

    gs = gridspec.GridSpec(
        3, 1,
        figure=fig,
        hspace=0.45,
        left=0.07, right=0.97,
        top=0.94, bottom=0.06,
        height_ratios=[3, 1.5, 1.5],
    )

    # ------------------------------------------------------------------ #
    #  Plot 1 — Brightness vs UTC time                                    #
    # ------------------------------------------------------------------ #
    ax1 = fig.add_subplot(gs[0])

    # Relative time (seconds from session start) is easier to read
    t0 = min(utc1[0], utc2[0])
    rel1 = utc1 - t0
    rel2 = utc2 - t0

    ax1.plot(rel1, bright1, color=CAM1_COLOR,  alpha=0.85, label="Camera 1")
    ax1.plot(rel2, bright2, color=CAM2_COLOR,  alpha=0.85, label="Camera 2")

    if sine_params is not None:
        # Reconstruct ground truth on a fine grid
        t_end   = max(rel1[-1], rel2[-1])
        t_grid  = np.linspace(0, t_end, int(t_end * 200))
        utc_grid = t_grid + t0
        gt      = ground_truth_sine(utc_grid, sine_params)
        ax1.plot(t_grid, gt, color=TRUTH_COLOR, linestyle="--",
                 linewidth=1.0, alpha=0.7, label="Ground-truth sine")

    ax1.set_title("ROI Brightness vs. Time", fontsize=10, pad=6)
    ax1.set_xlabel("Elapsed time  (s)")
    ax1.set_ylabel("Mean brightness  (0–255)")
    ax1.set_ylim(-5, 265)
    ax1.xaxis.set_minor_locator(AutoMinorLocator())
    ax1.legend(loc="upper right", fontsize=9)

    # Annotate period if sine params available
    if sine_params is not None:
        period = 1.0 / sine_params["freq_hz"]
        ax1.annotate(
            f"f = {sine_params['freq_hz']:.4f} Hz  ·  T = {period:.2f} s",
            xy=(0.02, 0.04), xycoords="axes fraction",
            fontsize=8, color="#888888",
        )

    # ------------------------------------------------------------------ #
    #  Plot 2 — Inter-camera timestamp delta                              #
    # ------------------------------------------------------------------ #
    ax2 = fig.add_subplot(gs[1])

    delta_ms = nearest_delta_ms(utc1, utc2)

    ax2.plot(rel1, delta_ms, color=DELTA_COLOR, alpha=0.8, linewidth=1.0)
    ax2.axhline(0, color="#555555", linewidth=0.7, linestyle="--")

    # Rolling mean for trend visibility
    if len(delta_ms) > 20:
        kernel = np.ones(15) / 15
        roll   = np.convolve(delta_ms, kernel, mode="same")
        ax2.plot(rel1, roll, color="#ffffff", linewidth=1.2,
                 alpha=0.5, label="15-frame rolling mean")
        ax2.legend(loc="upper right", fontsize=8)

    ax2.set_title("Inter-camera Timestamp Delta  (cam1 − nearest cam2)", fontsize=10, pad=6)
    ax2.set_xlabel("Elapsed time  (s)")
    ax2.set_ylabel("Delta  (ms)")
    ax2.xaxis.set_minor_locator(AutoMinorLocator())

    # Stats annotation
    stats_txt = (
        f"mean={np.mean(delta_ms):+.2f} ms    "
        f"std={np.std(delta_ms):.2f} ms    "
        f"p95={np.percentile(np.abs(delta_ms), 95):.2f} ms"
    )
    ax2.annotate(stats_txt, xy=(0.02, 0.04), xycoords="axes fraction",
                 fontsize=8, color="#aaaaaa")

    # ------------------------------------------------------------------ #
    #  Plot 3 — Frame interval jitter                                     #
    # ------------------------------------------------------------------ #
    ax3 = fig.add_subplot(gs[2])

    intervals1 = np.diff(utc1) * 1000   # ms
    intervals2 = np.diff(utc2) * 1000

    ax3.plot(rel1[1:], intervals1, color=JITTER1_COLOR, alpha=0.7,
             linewidth=0.9, label="Camera 1")
    ax3.plot(rel2[1:], intervals2, color=JITTER2_COLOR, alpha=0.7,
             linewidth=0.9, label="Camera 2")

    # Expected interval line
    if sine_params is None:
        # Estimate from data
        expected_ms = float(np.median(intervals1))
    else:
        expected_ms = float(np.median(intervals1))   # always data-derived

    ax3.axhline(expected_ms, color="#ffffff", linewidth=0.8,
                linestyle=":", alpha=0.5, label=f"median {expected_ms:.1f} ms")

    ax3.set_title("Frame Interval Jitter", fontsize=10, pad=6)
    ax3.set_xlabel("Elapsed time  (s)")
    ax3.set_ylabel("Frame interval  (ms)")
    ax3.xaxis.set_minor_locator(AutoMinorLocator())
    ax3.legend(loc="upper right", fontsize=8)

    # Jitter stats
    j1 = f"cam1  std={np.std(intervals1):.2f} ms  max={np.max(intervals1):.1f} ms"
    j2 = f"cam2  std={np.std(intervals2):.2f} ms  max={np.max(intervals2):.1f} ms"
    ax3.annotate(f"{j1}    {j2}", xy=(0.02, 0.04), xycoords="axes fraction",
                 fontsize=8, color="#aaaaaa")

    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    session_dir: Path = args.session_dir.resolve()

    if not session_dir.is_dir():
        print(f"ERROR: Session directory not found: {session_dir}")
        sys.exit(1)

    print(f"[analyze_sync] Session: {session_dir}")

    # ---- Locate files ------------------------------------------------------
    ts1_path = session_dir / "camera1_timestamps.bin"
    ts2_path = session_dir / "camera2_timestamps.bin"
    v1_path  = session_dir / "camera1.mjpeg"
    v2_path  = session_dir / "camera2.mjpeg"

    for p in (ts1_path, ts2_path, v1_path, v2_path):
        if not p.exists():
            print(f"ERROR: Required file missing: {p}")
            sys.exit(1)

    # Sine params (optional but recommended)
    sine_params_path = args.sine_params or (session_dir / "sine_params.json")
    sine_params = None
    if sine_params_path.exists():
        sine_params = json.loads(sine_params_path.read_text())
        print(f"[analyze_sync] Sine params loaded from {sine_params_path}")
    else:
        print("[analyze_sync] WARNING: sine_params.json not found — "
              "ground-truth overlay will be skipped.")

    # ---- Load timestamps ---------------------------------------------------
    print("[analyze_sync] Loading timestamps...")
    mono1 = load_timestamps_bin(ts1_path)
    mono2 = load_timestamps_bin(ts2_path)
    print(f"  cam1: {len(mono1)} timestamps")
    print(f"  cam2: {len(mono2)} timestamps")

    # ---- Load anchors & convert to UTC ------------------------------------
    start_utc_s, start_mono_us = load_anchor(session_dir)
    utc1 = mono_us_to_utc(mono1, start_utc_s, start_mono_us)
    utc2 = mono_us_to_utc(mono2, start_utc_s, start_mono_us)

    # ---- Extract brightness -----------------------------------------------
    roi = tuple(args.roi) if args.roi else None

    print("[analyze_sync] Extracting brightness from camera 1 video...")
    bright1 = extract_brightness(v1_path, roi,
                                 max_frames=args.max_frames,
                                 verbose=args.verbose)

    print("[analyze_sync] Extracting brightness from camera 2 video...")
    bright2 = extract_brightness(v2_path, roi,
                                 max_frames=args.max_frames,
                                 verbose=args.verbose)

    # ---- Align lengths (timestamps may have more entries than decoded frames)
    n1 = min(len(mono1), len(bright1))
    n2 = min(len(mono2), len(bright2))
    utc1, bright1 = utc1[:n1], bright1[:n1]
    utc2, bright2 = utc2[:n2], bright2[:n2]

    print(f"[analyze_sync] Aligned: cam1={n1} frames, cam2={n2} frames")

    # ---- Build figure ------------------------------------------------------
    session_label = session_dir.name
    print("[analyze_sync] Rendering plots...")
    fig = make_figure(utc1, bright1, utc2, bright2, sine_params, session_label)

    if args.output:
        fig.savefig(args.output, dpi=150, bbox_inches="tight")
        print(f"[analyze_sync] Figure saved → {args.output}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
