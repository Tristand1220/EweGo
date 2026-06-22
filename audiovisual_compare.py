#!/usr/bin/env python3
"""
analyze_sync.py — AV synchronization analyzer for dual_cam_jp2_hw.py sessions.

Reads a recording session directory and an optional sawtooth_params.json
produced by sawtooth_display.py, then generates two figures:

  FIGURE 1 — Camera analysis (3 plots)
    Plot 1 — ROI brightness vs. UTC time  (cam1, cam2, ground-truth sawtooth)
    Plot 2 — Inter-camera timestamp delta  (cam1_ts − nearest cam2_ts, ms)
    Plot 3 — Frame interval jitter per camera

  FIGURE 2 — Audio analysis (3 plots)  [only when an audio file is present]
    Plot 4 — RMS envelope vs. UTC time   (mic signal + ground-truth sawtooth)
    Plot 5 — Instantaneous amplitude envelope  (peak |signal| in 5 ms windows,
                                                shows the sawtooth ramp shape)
    Plot 6 — Spectrogram                 (confirms sawtooth pitch over time)

Usage:
    python3 analyze_sync.py SESSION_DIR [OPTIONS]

Options:
    --params PATH        Path to sawtooth_params.json
                         (also accepts legacy sine_params.json)
                         (default: SESSION_DIR/sawtooth_params.json)
    --audio PATH         Path to recorded audio file (WAV, FLAC, or raw PCM)
                         (default: SESSION_DIR/audio.wav)
    --audio-start-utc S  UTC epoch seconds when audio recording started.
                         Required if the audio file has no embedded timestamp.
    --roi X Y W H        ROI for brightness sampling (default: centre 100×100)
    --max-frames INT     Cap frames decoded per camera (0 = all)
    --output-prefix PATH Save figures to <prefix>_camera.png and <prefix>_audio.png
                         instead of showing interactively
    --no-html            Skip generating the interactive Plotly HTML version
                         of the ROI brightness plot (hover tooltips, 5-decimal
                         timestamps). Written by default as
                         <prefix or session_dir>_brightness.html
    --verbose            Print per-frame data to stdout
"""

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:
    print("opencv-python not installed.  pip install opencv-python")
    sys.exit(1)

try:
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    from matplotlib.ticker import AutoMinorLocator
except ImportError:
    print("matplotlib not installed.  pip install matplotlib")
    sys.exit(1)

try:
    import plotly.graph_objects as go
    PLOTLY_OK = True
except ImportError:
    PLOTLY_OK = False

# scipy is optional — used for spectrogram only
try:
    from scipy.io import wavfile as _wavfile
    from scipy.signal import spectrogram as _spectrogram
    SCIPY_OK = True
except ImportError:
    SCIPY_OK = False


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

plt.rcParams.update({
    "figure.facecolor":   "#0d0d0d",
    "axes.facecolor":     "#141414",
    "axes.edgecolor":     "#333333",
    "axes.labelcolor":    "#cccccc",
    "axes.titlecolor":    "#eeeeee",
    "axes.grid":          True,
    "grid.color":         "#2a2a2a",
    "grid.linewidth":     0.8,
    "xtick.color":        "#888888",
    "ytick.color":        "#888888",
    "text.color":         "#cccccc",
    "legend.facecolor":   "#1a1a1a",
    "legend.edgecolor":   "#333333",
    "legend.labelcolor":  "#cccccc",
    "lines.linewidth":    1.4,
    "font.family":        "monospace",
    "image.cmap":         "inferno",
})

CAM1_COLOR    = "#4fc3f7"
CAM2_COLOR    = "#ffb74d"
TRUTH_COLOR   = "#aaaaaa"
DELTA_COLOR   = "#ef5350"
AUDIO_COLOR   = "#a5d6a7"
AUDIO2_COLOR  = "#ce93d8"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("session_dir", type=Path)
    p.add_argument("--params", type=Path, default=None,
                   help="Path to sawtooth_params.json (or sine_params.json)")
    p.add_argument("--audio", type=Path, default=None,
                   help="Path to recorded audio file")
    p.add_argument("--audio-start-utc", type=float, default=None,
                   help="UTC epoch seconds when audio recording began")
    p.add_argument("--roi", type=int, nargs=4, default=None,
                   metavar=("X", "Y", "W", "H"))
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--output-prefix", type=Path, default=None,
                   help="Save figures as <prefix>_camera.png and <prefix>_audio.png")
    p.add_argument("--no-html", action="store_true",
                   help="Skip generating the interactive Plotly HTML "
                        "(<prefix or session_dir>_brightness.html)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Params loading  (sawtooth_params.json  OR  legacy sine_params.json)
# ---------------------------------------------------------------------------

def load_params(session_dir: Path, override: Path | None) -> dict | None:
    """
    Load sawtooth_params.json (preferred) or sine_params.json (legacy).
    Returns a normalised dict with keys:
        visual_freq_hz, visual_waveform,
        audio_envelope_freq_hz  (None if absent — the rate the AUDIO RMS
                                 should ramp at; matches visual_freq_hz when
                                 audio is amplitude-modulated by the same
                                 sawtooth)
        audio_carrier_freq_hz   (None if absent — audible pitch only,
                                 carries no timing information)
        start_utc_s     (float, epoch seconds)
    """
    candidates = []
    if override:
        candidates = [override]
    else:
        candidates = [
            session_dir / "sawtooth_params.json",
            session_dir / "sine_params.json",
        ]

    for path in candidates:
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        print(f"[analyze_sync] Params loaded from {path}")

        # Normalise sawtooth_params.json format
        if "visual" in raw:
            t0 = _parse_utc(raw["start_utc"])
            audio_raw = raw.get("audio", {})

            # New schema (amplitude-modulated): envelope_freq_hz / carrier_freq_hz
            # Old schema (independent pitch):    freq_hz only
            envelope_freq = audio_raw.get("envelope_freq_hz")
            carrier_freq  = audio_raw.get("carrier_freq_hz")
            if envelope_freq is None and "freq_hz" in audio_raw:
                # Legacy sawtooth_params.json from before the AM fix —
                # that freq_hz was the (constant-pitch) carrier; there was
                # no real envelope, so there's nothing meaningful to plot
                # against the visual signal.
                carrier_freq  = audio_raw.get("freq_hz")
                envelope_freq = None

            # For sawtooth
            return {
                "visual_freq_hz":         raw["visual"]["freq_hz"],
                "visual_waveform":        raw["visual"]["waveform"],
                "audio_envelope_freq_hz": envelope_freq,
                "audio_carrier_freq_hz":  carrier_freq,
                "start_utc_s":            t0,
                "_raw":                   raw,
            }

        # Legacy sine_params.json format (visual-only, no audio at all)
        t0 = _parse_utc(raw["start_utc"])
        return {
            "visual_freq_hz":         raw["freq_hz"],
            "visual_waveform":        "sine",
            "audio_envelope_freq_hz": None,
            "audio_carrier_freq_hz":  None,
            "start_utc_s":            t0,
            "_raw":                   raw,
        }

    print("[analyze_sync] WARNING: no params file found — "
          "ground-truth overlay will be skipped.")
    return None


def _parse_utc(s: str) -> float:
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ---------------------------------------------------------------------------
# Ground-truth reconstruction
# ---------------------------------------------------------------------------

def ground_truth_visual(utc_times: np.ndarray, params: dict) -> np.ndarray:
    """
    Reconstruct the ground-truth visual brightness (0–255) for an array of
    UTC epoch seconds. Supports 'sawtooth_forward' and 'sine' waveforms.
    """
    elapsed = utc_times - params["start_utc_s"]
    freq    = params["visual_freq_hz"]

    if "sawtooth" in params["visual_waveform"]:
        # B(t) = 255 * ((elapsed * freq) % 1.0)
        return 255.0 * np.mod(elapsed * freq, 1.0)
    else:
        # Legacy sine: B(t) = 127.5 * (1 + sin(2π·f·t))
        raw = params["_raw"]
        amp = raw.get("amplitude", 127.5)
        off = raw.get("offset",    127.5)
        ph  = raw.get("phase_rad", 0.0)
        return off + amp * np.sin(2 * math.pi * freq * elapsed + ph)


def ground_truth_audio_envelope(utc_times: np.ndarray, params: dict) -> "np.ndarray | None":
    """
    Reconstruct the ground-truth AUDIO ENVELOPE (0.0-1.0, NOT yet scaled by
    volume) for an array of UTC epoch seconds. This is the same-shaped
    sawtooth as the visual signal, since sawtooth_display.py amplitude-
    modulates the carrier tone by the visual brightness envelope.

    Returns None if this session's params don't carry envelope info
    (e.g. legacy sawtooth_params.json from before the AM fix, or no audio
    params at all).
    """
    if params.get("audio_envelope_freq_hz") is None:
        return None

    elapsed = utc_times - params["start_utc_s"]
    freq    = params["audio_envelope_freq_hz"]
    return np.mod(elapsed * freq, 1.0)   # 0.0 -> 1.0 ramp, same shape as video


def ground_truth_audio_rms_expected(params: dict) -> "float | None":
    """
    Theoretical time-averaged RMS of volume * envelope(t) * sin(carrier*t)
    for a sawtooth envelope and a much-faster sine carrier. Used only as a
    rough reference line; the real diagnostic is the shape-matching in
    Plot 7 (AV comparison), not this single number.

    RMS of envelope(t)=ramp[0,1) is 1/sqrt(3); RMS of unit sine carrier is
    1/sqrt(2); for carrier frequency >> envelope frequency these multiply
    independently to good approximation.
    """
    if params.get("audio_envelope_freq_hz") is None:
        return None
    volume = params.get("_raw", {}).get("audio", {}).get("volume", 0.8)
    return volume * (1.0 / math.sqrt(3)) * (1.0 / math.sqrt(2))


# ---------------------------------------------------------------------------
# Timestamp file helpers
# ---------------------------------------------------------------------------

def load_timestamps_bin(path: Path) -> np.ndarray:
    data = path.read_bytes()
    n    = len(data) // 8
    if n == 0:
        raise ValueError(f"Timestamp file is empty: {path}")
    return np.frombuffer(data[:n * 8], dtype="<i8").copy()


def load_anchor(session_dir: Path):
    wall_str = (session_dir / "start_time.txt").read_text().strip()
    mono_str = (session_dir / "start_time_mono_us.txt").read_text().strip()
    try:
        dt = datetime.fromisoformat(wall_str)
    except ValueError:
        dt = datetime.fromisoformat(wall_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), int(mono_str)

def load_anchor_audio(session_dir: Path):
    wall_str = (session_dir / "audio_t0_ns.txt").read_text().strip()
    mono_str = (session_dir / "audio_start_time_mono_us.txt").read_text().strip()
    try:
        dt = datetime.fromisoformat(wall_str)
    except ValueError:
        dt = datetime.fromisoformat(wall_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), int(mono_str)


def mono_us_to_utc(mono_us_arr: np.ndarray,
                   start_utc_s: float,
                   start_mono_us: int) -> np.ndarray:
    return start_utc_s + (mono_us_arr - start_mono_us) / 1e6


# ---------------------------------------------------------------------------
# Video brightness extraction
# ---------------------------------------------------------------------------

def extract_brightness(mjpeg_path: Path, roi: tuple | None,
                       max_frames: int = 0,
                       verbose: bool = False) -> np.ndarray:
    cap = cv2.VideoCapture(str(mjpeg_path))
    if not cap.isOpened():
        raise IOError(f"Cannot open: {mjpeg_path}")

    values    = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if roi is None:
            h, w   = frame.shape[:2]
            cx, cy = w // 2, h // 2
            roi    = (cx - 50, cy - 50, 100, 100)

        x, y, rw, rh = roi
        fh, fw = frame.shape[:2]
        x  = max(0, min(x,  fw - 1))
        y  = max(0, min(y,  fh - 1))
        rw = max(1, min(rw, fw - x))
        rh = max(1, min(rh, fh - y))

        patch = frame[y:y+rh, x:x+rw]
        gray  = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
        mean  = float(np.mean(gray))
        values.append(mean)

        if verbose:
            print(f"  frame {frame_idx:5d}  brightness={mean:6.2f}")

        frame_idx += 1
        if max_frames > 0 and frame_idx >= max_frames:
            break

    cap.release()
    return np.array(values, dtype=np.float32)


# ---------------------------------------------------------------------------
# Audio loading and analysis
# ---------------------------------------------------------------------------

def load_audio_sidecar(session_dir: Path,
                       audio_path: Path) -> "float | None":
    """
    Locate and parse the audio t0 sidecar JSON produced by the recorder script.

    Search order:
      1. <audio_stem>_t0.json  (alongside the audio file)
      2. SESSION_DIR/audio_t0.json
      3. SESSION_DIR/t0_sidecar.json

    The sidecar contains {"t0_ns": <int>} where t0_ns is time.time_ns()
    captured just before arecord was launched (wall-clock nanoseconds).

    Returns UTC epoch seconds (float), or None if no sidecar found.
    """
    candidates = [
        audio_path.parent / (audio_path.stem + "_t0.json"),
        session_dir / "audio_t0.json",
        session_dir / "t0_sidecar.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text())
                t0_s = data["t0_ns"] / 1e9
                print(f"[analyze_sync] Audio sidecar loaded from {path}")
                print(f"  t0 = {t0_s:.6f} s (UTC epoch)  "
                      f"approx {datetime.fromtimestamp(t0_s, tz=timezone.utc).isoformat()}")
                return t0_s
            except Exception as e:
                print(f"  [WARN] Could not parse sidecar {path}: {e}")
    return None


def load_audio(audio_path: Path) -> "tuple[np.ndarray, int]":
    """
    Load audio from a WAV file recorded by arecord (S32_LE stereo 48 kHz)
    or any other WAV/FLAC/raw-PCM file.

    Returns (samples_float32_mono, sample_rate).

    S32_LE normalisation (arecord 32-bit signed):
        float = int32 / 2^31   (NOT iinfo(int32).max = 2^31-1)
    """
    suffix = audio_path.suffix.lower()

    if suffix in (".wav", ".flac") and SCIPY_OK:
        sr, data = _wavfile.read(str(audio_path))
        if data.ndim > 1:
            data = data[:, 0]          # stereo -> mono (first channel)
        if data.dtype == np.int32:
            # S32_LE from arecord
            data = data.astype(np.float32) / 2_147_483_648.0
        elif data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype != np.float32:
            data = data.astype(np.float32) / float(np.iinfo(data.dtype).max)
        return data, sr

    if suffix == ".wav":
        # Minimal WAV reader fallback (no scipy)
        import wave
        with wave.open(str(audio_path)) as wf:
            sr       = wf.getframerate()
            n_frames = wf.getnframes()
            n_ch     = wf.getnchannels()
            sw       = wf.getsampwidth()
            raw      = wf.readframes(n_frames)
        dtype_map = {1: np.int8, 2: np.int16, 4: np.int32}
        samp = np.frombuffer(raw, dtype=dtype_map.get(sw, np.int16))
        if n_ch > 1:
            samp = samp[::n_ch]
        peak = float(2 ** (8 * sw - 1))
        return samp.astype(np.float32) / peak, sr

    # Raw float32 little-endian PCM -- assume 48000 Hz mono
    data = np.frombuffer(audio_path.read_bytes(), dtype="<f4")
    return data, 48000



def compute_rms_envelope(samples: np.ndarray, sr: int,
                         window_s: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute RMS amplitude in non-overlapping windows of window_s seconds.
    Returns (rms_values, window_centre_offsets_seconds).
    """
    win   = max(1, int(sr * window_s))
    n_win = len(samples) // win
    samp  = samples[:n_win * win].reshape(n_win, win)
    rms   = np.sqrt(np.mean(samp ** 2, axis=1))
    centres = (np.arange(n_win) + 0.5) * window_s
    return rms, centres


# ---------------------------------------------------------------------------
# Nearest-neighbour inter-camera delta
# ---------------------------------------------------------------------------

def nearest_delta_ms(ts_a: np.ndarray, ts_b: np.ndarray) -> np.ndarray:
    idx   = np.searchsorted(ts_b, ts_a, side="left")
    idx   = np.clip(idx, 0, len(ts_b) - 1)
    idx_m = np.clip(idx - 1, 0, len(ts_b) - 1)
    diff0 = np.abs(ts_a - ts_b[idx])
    diffm = np.abs(ts_a - ts_b[idx_m])
    near  = np.where(diffm < diff0, ts_b[idx_m], ts_b[idx])
    return (ts_a - near) * 1000.0


# ---------------------------------------------------------------------------
# Shared x-axis — dynamic unit formatter
# ---------------------------------------------------------------------------
#
# All data is stored in elapsed SECONDS (float64).  As the user zooms in,
# the visible x-span shrinks and the formatter automatically switches units:
#
#   span > 1 s    ->  display as   s    e.g.  "12.5 s"
#   span > 1 ms   ->  display as  ms    e.g.  "450 ms"
#   span <= 1 ms  ->  display as  us    e.g.  "312 us"
#
# The unit label in the axis title is also updated on every draw so it
# always matches the current zoom level.
# ---------------------------------------------------------------------------

def _make_dynamic_formatter(ax, base_xlabel: str = "Elapsed time"):
    """
    Attach a FuncFormatter + AutoLocator to `ax` that switches between
    s / ms / us depending on the visible x-span at draw time.

    The x-data must be in seconds.  The formatter multiplies tick values
    by the appropriate scale factor and appends the unit suffix.
    A callback on xlim_changed keeps the axis label in sync.
    """
    from matplotlib.ticker import FuncFormatter, AutoLocator

    def _fmt(val_s, _pos):
        lo, hi = ax.get_xlim()
        span   = hi - lo
        if span > 1.0:
            return f"{val_s:.3g} s"
        elif span > 1e-3:
            return f"{val_s * 1e3:.4g} ms"
        else:
            return f"{val_s * 1e6:.4g} μs"

    def _update_label(_ax):
        lo, hi = _ax.get_xlim()
        span   = hi - lo
        if span > 1.0:
            unit = "s"
        elif span > 1e-3:
            unit = "ms"
        else:
            unit = "μs"
        _ax.set_xlabel(f"{base_xlabel}  ({unit})")

    ax.xaxis.set_major_locator(AutoLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(_fmt))
    ax.callbacks.connect("xlim_changed", _update_label)
    _update_label(ax)


def _apply_elapsed_xaxis(ax, span_s: float):
    """
    Set up the x-axis for an elapsed-time plot (data in seconds).
    Initial tick density is chosen for the full recording span, but
    the formatter dynamically switches s/ms/us as the user zooms in.
    """
    if span_s < 60:
        step = 5.0
    elif span_s < 300:
        step = 30.0
    elif span_s < 600:
        step = 60.0
    else:
        step = 120.0

    ax.xaxis.set_major_locator(plt.MultipleLocator(step))
    ax.xaxis.set_minor_locator(plt.MultipleLocator(step / 5))
    _make_dynamic_formatter(ax)


# ---------------------------------------------------------------------------
# Figure 1 — Camera sync
# ---------------------------------------------------------------------------

def make_camera_figure(utc1, bright1, utc2, bright2, params, session_label):
    fig = plt.figure(figsize=(16, 11))
    fig.suptitle(
        f"Camera Sync Analysis  ·  Session: {session_label}",
        fontsize=13, fontweight="bold", y=0.98, color="#eeeeee",
    )
    gs = gridspec.GridSpec(
        3, 1, figure=fig, hspace=0.55,
        left=0.07, right=0.97, top=0.94, bottom=0.08,
        height_ratios=[3, 1.5, 1.5],
    )

    # All x-axes use elapsed seconds from the earliest frame in this session
    t0     = min(utc1[0], utc2[0])
    rel1   = utc1 - t0
    rel2   = utc2 - t0
    span_s = max(rel1[-1], rel2[-1])

    # ---- Plot 1: Connected scatter — one dot per captured frame ------------
    ax1 = fig.add_subplot(gs[0])

    # Ground-truth drawn first so camera dots sit on top of it
    if params is not None:
        t_grid = np.linspace(0, span_s, int(span_s * 200))
        gt     = ground_truth_visual(t_grid + t0, params)
        ax1.plot(t_grid, gt, "--", color=TRUTH_COLOR,
                 linewidth=1.0, alpha=0.6, zorder=1,
                 label=f"Ground-truth ({params['visual_waveform']})")
        freq   = params["visual_freq_hz"]
        period = 1.0 / freq
        ax1.annotate(
            f"f = {freq:.4f} Hz  T = {period:.2f} s",
            xy=(0.02, 0.04), xycoords="axes fraction",
            fontsize=8, color="#888888",
        )

    # Connected scatter: line shows order, dot marks exact timestamp
    ax1.plot(rel1, bright1, "-o",
             color=CAM1_COLOR, alpha=0.85,
             linewidth=0.8, markersize=4,
             markerfacecolor=CAM1_COLOR, markeredgewidth=0,
             zorder=3, label="Camera 1")
    ax1.plot(rel2, bright2, "-o",
             color=CAM2_COLOR, alpha=0.85,
             linewidth=0.8, markersize=4,
             markerfacecolor=CAM2_COLOR, markeredgewidth=0,
             zorder=2, label="Camera 2")

    ax1.set_title("ROI Brightness vs. Elapsed Time  (each dot = one captured frame)",
                  fontsize=10, pad=6)
    ax1.set_ylabel("Mean brightness  (0-255)")
    ax1.set_ylim(-5, 265)
    ax1.legend(loc="upper right", fontsize=9)
    _apply_elapsed_xaxis(ax1, span_s)

    # ---- Plot 2: Inter-camera delta ----------------------------------------
    ax2   = fig.add_subplot(gs[1])
    delta = nearest_delta_ms(utc1, utc2)
    ax2.plot(rel1, delta, "-", color=DELTA_COLOR, alpha=0.8, linewidth=1.0)
    ax2.axhline(0, color="#555555", linewidth=0.7, linestyle="--")
    if len(delta) > 20:
        roll = np.convolve(delta, np.ones(15) / 15, mode="same")
        ax2.plot(rel1, roll, "-", color="#ffffff",
                 linewidth=1.2, alpha=0.5, label="15-frame rolling mean")
        ax2.legend(loc="upper right", fontsize=8)
    ax2.set_title("Inter-camera Timestamp Delta  (cam1 - nearest cam2)",
                  fontsize=10, pad=6)
    ax2.set_ylabel("Delta  (ms)")
    _apply_elapsed_xaxis(ax2, span_s)
    ax2.annotate(
        f"mean={np.mean(delta):+.2f} ms    "
        f"std={np.std(delta):.2f} ms    "
        f"p95={np.percentile(np.abs(delta), 95):.2f} ms",
        xy=(0.02, 0.04), xycoords="axes fraction", fontsize=8, color="#aaaaaa",
    )

    # ---- Plot 3: Per-camera wall-clock residual ----------------------------
    #
    # For each frame i, we know two things:
    #   - The Pi's monotonic timestamp (already converted to UTC via the
    #     anchor pair in start_time.txt / start_mono_us.txt)
    #   - What the Pi's wall clock *predicts* that frame's time should be,
    #     based on a perfectly uniform frame rate:
    #       predicted_utc[i] = t0 + i * median_interval
    #
    # The residual is:  actual_utc[i] - predicted_utc[i]
    # plotted in milliseconds for both cameras independently.
    #
    # A flat line near zero means the camera is delivering frames exactly
    # on the schedule the Pi's clock expects — ideal sync.
    # A slope means the camera's clock is running faster or slower than
    # the Pi wall clock (drift).
    # Sudden jumps are dropped frames.
    # The two cameras sharing the same residual shape means they drift
    # together (system issue); diverging shapes means per-camera timing
    # instability.

    ax3 = fig.add_subplot(gs[2])

    # Build the ideal uniform-rate timeline for each camera independently,
    # anchored to that camera's first frame and using its median interval
    # so a single early dropped frame doesn't skew the whole baseline.
    median_interval1 = float(np.median(np.diff(utc1)))
    median_interval2 = float(np.median(np.diff(utc2)))

    predicted1 = utc1[0] + np.arange(len(utc1)) * median_interval1
    predicted2 = utc2[0] + np.arange(len(utc2)) * median_interval2

    residual1_ms = (utc1 - predicted1) * 1000
    residual2_ms = (utc2 - predicted2) * 1000

    ax3.plot(rel1, residual1_ms, "-", color=CAM1_COLOR,
             alpha=0.85, linewidth=0.9, label="Camera 1")
    ax3.plot(rel2, residual2_ms, "-", color=CAM2_COLOR,
             alpha=0.85, linewidth=0.9, label="Camera 2")
    ax3.axhline(0, color="#555555", linewidth=0.7, linestyle="--")

    # Rolling mean on each to expose slow drift trend vs. high-freq jitter
    if len(residual1_ms) > 20:
        roll1 = np.convolve(residual1_ms, np.ones(15) / 15, mode="same")
        roll2 = np.convolve(residual2_ms, np.ones(15) / 15, mode="same")
        ax3.plot(rel1, roll1, "-", color=CAM1_COLOR,
                 linewidth=1.8, alpha=0.4, label="Cam1 trend")
        ax3.plot(rel2, roll2, "-", color=CAM2_COLOR,
                 linewidth=1.8, alpha=0.4, label="Cam2 trend")

    ax3.set_title(
        "Per-camera Wall-clock Residual  "
        "(actual timestamp - predicted from Pi UTC baseline)",
        fontsize=10, pad=6,
    )
    ax3.set_ylabel("Residual  (ms)")
    _apply_elapsed_xaxis(ax3, span_s)
    ax3.legend(loc="upper right", fontsize=8)

    # Drift rate in ms/min — slope of a linear fit over the residuals
    def _drift_rate(rel_s, residual_ms):
        if len(rel_s) < 2:
            return 0.0
        coeffs = np.polyfit(rel_s, residual_ms, 1)   # slope in ms/s
        return coeffs[0] * 60                          # convert to ms/min

    dr1 = _drift_rate(rel1, residual1_ms)
    dr2 = _drift_rate(rel2, residual2_ms)
    ax3.annotate(
        f"cam1 drift={dr1:+.3f} ms/min    "
        f"cam2 drift={dr2:+.3f} ms/min    "
        f"(fitted linear slope over full recording)",
        xy=(0.02, 0.04), xycoords="axes fraction", fontsize=8, color="#aaaaaa",
    )

    return fig


# ---------------------------------------------------------------------------
# Interactive Plotly version of Plot 1 — ROI brightness with full-precision
# hover tooltips (timestamps to 5 decimal places)
# ---------------------------------------------------------------------------

def make_brightness_html(utc1: np.ndarray, bright1: np.ndarray,
                         utc2: np.ndarray, bright2: np.ndarray,
                         params: dict | None, session_label: str):
    """
    Build an interactive Plotly version of the ROI brightness scatter
    (Plot 1 from the matplotlib camera figure).

    Hovering over any point shows:
      - Elapsed time to 5 decimal places (i.e. down to 10 microseconds)
      - The absolute UTC wall-clock time for that frame
      - The frame's brightness value
      - Which camera captured it

    Returns a plotly.graph_objects.Figure, or None if plotly is unavailable.
    """
    if not PLOTLY_OK:
        print("[analyze_sync] plotly not installed — skipping interactive "
              "HTML output.  pip install plotly")
        return None

    t0     = min(utc1[0], utc2[0])
    rel1   = utc1 - t0
    rel2   = utc2 - t0
    span_s = max(rel1[-1], rel2[-1])

    fig = go.Figure()

    # ---- Ground truth (drawn first, sits behind the camera traces) --------
    if params is not None:
        t_grid   = np.linspace(0, span_s, int(span_s * 200))
        gt       = ground_truth_visual(t_grid + t0, params)
        fig.add_trace(go.Scatter(
            x=t_grid, y=gt,
            mode="lines",
            name=f"Ground-truth ({params['visual_waveform']})",
            line=dict(color=TRUTH_COLOR, width=1.2, dash="dash"),
            opacity=0.6,
            hoverinfo="skip",   # reference line — frame hover is the point
        ))

    # ---- Helper to build the per-point hover text with 5-decimal time -----
    def _hover_text(rel_s: np.ndarray, utc_s: np.ndarray,
                    bright: np.ndarray, cam_label: str) -> list:
        texts = []
        for r, u, b in zip(rel_s, utc_s, bright):
            utc_dt = datetime.fromtimestamp(u, tz=timezone.utc)
            texts.append(
                f"{cam_label}<br>"
                f"Elapsed: {r:.5f} s<br>"
                f"UTC: {utc_dt.strftime('%H:%M:%S.%f')[:-1]}<br>"
                f"Brightness: {b:.2f}"
            )
        return texts

    # ---- Camera 1 -----------------------------------------------------------
    fig.add_trace(go.Scatter(
        x=rel1, y=bright1,
        mode="lines+markers",
        name="Camera 1",
        line=dict(color=CAM1_COLOR, width=1.2),
        marker=dict(color=CAM1_COLOR, size=5, line=dict(width=0)),
        opacity=0.9,
        text=_hover_text(rel1, utc1, bright1, "Camera 1"),
        hoverinfo="text",
    ))

    # ---- Camera 2 -----------------------------------------------------------
    fig.add_trace(go.Scatter(
        x=rel2, y=bright2,
        mode="lines+markers",
        name="Camera 2",
        line=dict(color=CAM2_COLOR, width=1.2),
        marker=dict(color=CAM2_COLOR, size=5, line=dict(width=0)),
        opacity=0.9,
        text=_hover_text(rel2, utc2, bright2, "Camera 2"),
        hoverinfo="text",
    ))

    # ---- Layout: dark theme matching the matplotlib figures ----------------
    title_extra = ""
    if params is not None:
        freq   = params["visual_freq_hz"]
        period = 1.0 / freq
        title_extra = f"   ·   f = {freq:.4f} Hz   T = {period:.2f} s"

    fig.update_layout(
        title=dict(
            text=(f"ROI Brightness vs. Elapsed Time  "
                  f"(hover for exact timestamp)  ·  Session: {session_label}"
                  f"{title_extra}"),
            font=dict(size=14, color="#eeeeee"),
        ),
        paper_bgcolor="#0d0d0d",
        plot_bgcolor="#141414",
        font=dict(color="#cccccc", family="monospace"),
        xaxis=dict(
            title="Elapsed time (s)  —  drag to zoom, double-click to reset",
            gridcolor="#2a2a2a",
            zerolinecolor="#333333",
            # Plotly auto-formats tick density on zoom; for sub-second zoom
            # the hover text remains the source of true precision since
            # tick labels alone can't show 5 decimal places at all scales.
        ),
        yaxis=dict(
            title="Mean brightness (0-255)",
            range=[-5, 265],
            gridcolor="#2a2a2a",
            zerolinecolor="#333333",
        ),
        legend=dict(
            bgcolor="#1a1a1a",
            bordercolor="#333333",
            borderwidth=1,
            font=dict(color="#cccccc"),
        ),
        hovermode="closest",
        hoverlabel=dict(
            bgcolor="#1a1a1a",
            font_size=12,
            font_family="monospace",
            bordercolor="#444444",
        ),
        height=600,
        margin=dict(l=60, r=30, t=70, b=60),
    )

    return fig



def make_audio_figure(audio_samples: np.ndarray, audio_sr: int,
                      audio_start_utc: float,
                      params: dict | None, session_label: str):
    """
    Three-panel audio analysis figure:
      Plot 4 — RMS envelope vs elapsed time        (50 ms windows, smoothed)
      Plot 5 — Instantaneous amplitude envelope    (5 ms peak-detection windows,
                                                    shows sawtooth ramp shape)
      Plot 6 — Spectrogram                         (confirms carrier pitch over time)
    """
    fig = plt.figure(figsize=(16, 11))
    fig.suptitle(
        f"Audio Sync Analysis  ·  Session: {session_label}",
        fontsize=13, fontweight="bold", y=0.98, color="#eeeeee",
    )
    gs = gridspec.GridSpec(
        3, 1, figure=fig, hspace=0.55,
        left=0.07, right=0.97, top=0.94, bottom=0.08,
        height_ratios=[2.5, 1.5, 2],
    )

    WINDOW_S = 0.05   # 50 ms RMS windows
    rms, centres_s = compute_rms_envelope(audio_samples, audio_sr, WINDOW_S)
    span_s         = centres_s[-1] - centres_s[0]

    # ---- Plot 4: RMS envelope ----------------------------------------------
    ax4 = fig.add_subplot(gs[0])
    ax4.plot(centres_s, rms, "-", color=AUDIO_COLOR,
             alpha=0.85, linewidth=1.0, label="Mic RMS (50 ms windows)")

    if params is not None and params.get("audio_envelope_freq_hz") is not None:
        expected_peak = ground_truth_audio_rms_expected(params)
        if expected_peak is not None:
            ax4.axhline(expected_peak, color=TRUTH_COLOR, linewidth=1.0,
                        linestyle="--", alpha=0.7,
                        label=f"Expected peak RMS (~{expected_peak:.3f})")
        ax4.annotate(
            f"envelope f = {params['audio_envelope_freq_hz']:.4f} Hz   "
            f"carrier f = {params.get('audio_carrier_freq_hz', 0):.1f} Hz",
            xy=(0.02, 0.04), xycoords="axes fraction",
            fontsize=8, color="#888888",
        )

    ax4.set_title("Microphone RMS Envelope vs. Elapsed Time", fontsize=10, pad=6)
    ax4.set_ylabel("RMS amplitude")
    ax4.legend(loc="upper right", fontsize=9)
    _apply_elapsed_xaxis(ax4, span_s)

    # ---- Plot 5: Instantaneous amplitude envelope --------------------------
    #
    # Peak envelope follower: slide a short window across the raw samples
    # and take max(abs()) within each window. This directly shows the
    # sawtooth volume shape — the ramp from silence to peak and instant
    # drop — without smoothing it into a flat RMS average. Uses a much
    # shorter window than Plot 4 (5 ms vs 50 ms) so the ramp edges stay
    # sharp and the sawtooth shape is visually clear.
    ENV_WINDOW_S = 0.005   # 5 ms peak-detection window
    env_win      = max(1, int(audio_sr * ENV_WINDOW_S))
    n_env_win    = len(audio_samples) // env_win
    env_blocks   = np.abs(
        audio_samples[:n_env_win * env_win].reshape(n_env_win, env_win)
    )
    peak_envelope = env_blocks.max(axis=1)
    env_centres_s = (np.arange(n_env_win) + 0.5) * ENV_WINDOW_S

    ax5 = fig.add_subplot(gs[1])
    ax5.plot(env_centres_s, peak_envelope, "-",
             color=AUDIO2_COLOR, alpha=0.85, linewidth=0.7,
             label=f"Peak envelope ({ENV_WINDOW_S*1000:.0f} ms window)")

    # Ground-truth sawtooth envelope overlay (scaled to volume)
    if params is not None and params.get("audio_envelope_freq_hz") is not None:
        gt_utc  = audio_start_utc + env_centres_s
        gt_env  = ground_truth_audio_envelope(gt_utc, params)
        if gt_env is not None:
            raw     = params.get("_raw", {})
            volume  = raw.get("audio", {}).get("volume", 0.8)
            # Expected peak amplitude of the envelope*carrier product:
            # when envelope=1.0 and carrier=±1.0, peak = volume
            # RMS of unit sine = 1/sqrt(2), so expected peak env ≈ volume
            ax5.plot(env_centres_s, gt_env * volume, "--",
                     color=TRUTH_COLOR, linewidth=1.0, alpha=0.6,
                     label="Ground-truth envelope")

    ax5.set_title(
        f"Instantaneous Amplitude Envelope  "
        f"(peak of |signal| in {ENV_WINDOW_S*1000:.0f} ms windows)",
        fontsize=10, pad=6,
    )
    ax5.set_ylabel("Peak amplitude")
    ax5.legend(loc="upper right", fontsize=8)
    _apply_elapsed_xaxis(ax5, span_s)
    ax5.annotate(
        f"peak={float(peak_envelope.max()):.4f}    "
        f"floor={float(np.percentile(peak_envelope, 5)):.4f}    "
        f"dynamic_range={20*np.log10(peak_envelope.max()/max(peak_envelope.min(),1e-9)):.1f} dB",
        xy=(0.02, 0.04), xycoords="axes fraction", fontsize=8, color="#aaaaaa",
    )

    # ---- Plot 6: Spectrogram -----------------------------------------------
    ax6 = fig.add_subplot(gs[2])

    if SCIPY_OK:
        nperseg = min(2048, len(audio_samples) // 8)
        f, t, Sxx = _spectrogram(audio_samples, fs=audio_sr,
                                 nperseg=nperseg, noverlap=nperseg // 2)
        # t is already elapsed seconds from start of the audio file
        max_freq = 4000
        if params and params.get("audio_carrier_freq_hz"):
            max_freq = min(4000, params["audio_carrier_freq_hz"] * 8)
        f_mask = f <= max_freq

        im = ax6.pcolormesh(
            t, f[f_mask],
            10 * np.log10(Sxx[f_mask] + 1e-12),
            shading="gouraud", cmap="inferno",
        )
        plt.colorbar(im, ax=ax6, label="dB", pad=0.01)

        if params and params.get("audio_carrier_freq_hz"):
            fund = params["audio_carrier_freq_hz"]
            for k in range(1, 9):
                hf = fund * k
                if hf > max_freq:
                    break
                ax6.axhline(hf, color="#ffffff", linewidth=0.6,
                            linestyle="--", alpha=0.4)

        ax6.set_title("Spectrogram (dB)", fontsize=10, pad=6)
        ax6.set_ylabel("Frequency  (Hz)")
        _apply_elapsed_xaxis(ax6, float(t[-1]))
    else:
        ax6.text(0.5, 0.5,
                 "scipy not installed — spectrogram unavailable.\n"
                 "pip install scipy",
                 ha="center", va="center", transform=ax6.transAxes,
                 color="#888888", fontsize=11)
        ax6.set_title("Spectrogram (unavailable)", fontsize=10, pad=6)

    return fig


# ---------------------------------------------------------------------------
# Figure 3 — Audio/Visual comparison
# ---------------------------------------------------------------------------
#
# This is the direct answer to "how do I compare audio and visual together?"
#
# Both signals are normalized to a common 0.0-1.0 scale and plotted on the
# SAME time axis:
#   - Video: ROI brightness, normalized by /255
#   - Audio: RMS envelope, normalized by its own observed peak
#
# Because sawtooth_display.py now amplitude-modulates the audio carrier by
# the exact same sawtooth driving the screen, both curves SHOULD trace the
# same ramp shape, frequency, and phase. Any visible lag between the two
# curves is the true end-to-end AV offset between your camera pipeline and
# your microphone capture pipeline, referenced to the same Pi UTC clock.
# ---------------------------------------------------------------------------

def make_av_comparison_figure(
    rel_video_s: np.ndarray, video_norm: np.ndarray,
    rel_audio_s: np.ndarray, audio_norm: np.ndarray,
    params: dict | None, session_label: str,
    rms_window_s: float = 0.005,
):
    """
    Single-panel figure overlaying normalized video brightness and
    normalized audio RMS envelope on a shared elapsed-time axis, plus
    a lower panel showing the AV offset estimated via cross-correlation.

    rms_window_s is the window used to compute the audio RMS envelope
    upstream of this function; finer windows give a more faithful audio
    envelope shape, which helps cross-correlation accuracy, but the
    dominant resolution floor on the measured lag is actually the common
    time grid step (common_dt below) combined with the video frame
    interval, since video uses zero-order-hold (step) resampling.
    """
    fig = plt.figure(figsize=(16, 8))
    fig.suptitle(
        f"Audio/Visual Comparison  ·  Session: {session_label}",
        fontsize=13, fontweight="bold", y=0.97, color="#eeeeee",
    )
    gs = gridspec.GridSpec(
        2, 1, figure=fig, hspace=0.5,
        left=0.07, right=0.97, top=0.90, bottom=0.10,
        height_ratios=[2.5, 1],
    )

    span_s = max(rel_video_s[-1], rel_audio_s[-1])

    # ---- Top panel: overlaid normalized signals -----------------------------
    ax1 = fig.add_subplot(gs[0])
    ax1.plot(rel_video_s, video_norm, "-o", color=CAM1_COLOR,
             alpha=0.85, linewidth=0.8, markersize=3,
             markerfacecolor=CAM1_COLOR, markeredgewidth=0,
             label="Video brightness (normalized)")
    ax1.plot(rel_audio_s, audio_norm, "-", color=AUDIO_COLOR,
             alpha=0.85, linewidth=1.0,
             label="Audio RMS envelope (normalized)")

    if params is not None:
        freq = params["visual_freq_hz"]
        ax1.annotate(
            f"shared envelope f = {freq:.4f} Hz  T = {1/freq:.2f} s",
            xy=(0.02, 0.04), xycoords="axes fraction",
            fontsize=8, color="#888888",
        )

    ax1.set_title(
        "Normalized Video Brightness vs. Audio RMS  (same time base)",
        fontsize=10, pad=6,
    )
    ax1.set_ylabel("Normalized amplitude  (0-1)")
    ax1.set_ylim(-0.05, 1.05)
    ax1.legend(loc="upper right", fontsize=9)
    _apply_elapsed_xaxis(ax1, span_s)

    # ---- Bottom panel: cross-correlation lag estimate ------------------------
    ax2 = fig.add_subplot(gs[1])

    # Resample both signals onto a common uniform time grid so cross-
    # correlation is meaningful (video and audio have different native
    # sample rates / irregular timestamps).
    #
    # IMPORTANT: video uses ZERO-ORDER-HOLD (step) interpolation, not linear.
    # Linear interpolation across a sawtooth's sharp reset invents a fake
    # ramp through the gap between two real video samples (e.g. a frame at
    # the peak followed by a frame at the trough gets linearly connected),
    # which biases the cross-correlation peak by tens of milliseconds.
    # Zero-order-hold instead holds each frame's value until the next frame
    # arrives — correctly representing "we don't know what happened between
    # samples" instead of inventing a transition that never occurred.
    common_dt  = 0.005   # 5 ms grid — also sets the lag resolution floor
    grid       = np.arange(0, span_s, common_dt)

    video_idx  = np.clip(
        np.searchsorted(rel_video_s, grid, side="right") - 1,
        0, len(video_norm) - 1,
    )
    video_grid = video_norm[video_idx]
    audio_grid = np.interp(grid, rel_audio_s, audio_norm)   # audio is densely
                                                            # sampled already,
                                                            # linear is fine here

    # Remove DC offset before correlating so the match is about shape, not level
    v = video_grid - np.mean(video_grid)
    a = audio_grid - np.mean(audio_grid)

    corr     = np.correlate(a, v, mode="full")
    lags     = np.arange(-len(v) + 1, len(v)) * common_dt
    peak_idx = np.argmax(corr)
    best_lag = lags[peak_idx]

    # Only show a window around zero lag (+/- 0.5 period or 1s, whichever larger)
    window = max(1.0, (1.0 / params["visual_freq_hz"]) * 0.75) if params else 1.0
    mask   = np.abs(lags) <= window

    ax2.plot(lags[mask] * 1000, corr[mask], "-", color=DELTA_COLOR,
             linewidth=1.0, alpha=0.85)
    ax2.axvline(0, color="#555555", linewidth=0.7, linestyle="--")
    ax2.axvline(best_lag * 1000, color="#ffffff", linewidth=1.0,
                linestyle=":", alpha=0.8,
                label=f"best-fit lag = {best_lag*1000:+.2f} ms")

    ax2.set_title(
        "Cross-correlation: Audio relative to Video "
        "(positive = audio lags video)",
        fontsize=10, pad=6,
    )
    ax2.set_xlabel("Lag  (ms)")
    ax2.set_ylabel("Correlation")
    ax2.legend(loc="upper right", fontsize=8)

    interpretation = (
        "audio arrives AFTER video" if best_lag > 0 else
        "audio arrives BEFORE video" if best_lag < 0 else
        "perfectly aligned"
    )
    ax2.annotate(
        f"AV offset: {best_lag*1000:+.2f} ms  ({interpretation})   "
        f"resolution limit: ~\u00b1{common_dt*1000:.0f} ms (grid step)",
        xy=(0.02, 0.04), xycoords="axes fraction", fontsize=8, color="#aaaaaa",
    )

    return fig



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args        = parse_args()
    session_dir = args.session_dir.resolve()

    if not session_dir.is_dir():
        print(f"ERROR: Session directory not found: {session_dir}")
        sys.exit(1)

    print(f"[analyze_sync] Session: {session_dir}")

    # ---- Load params -------------------------------------------------------
    params = load_params(session_dir, args.params)

    # ---- Locate camera files -----------------------------------------------
    ts1_path = session_dir / "camera1_timestamps.bin"
    ts2_path = session_dir / "camera2_timestamps.bin"
    v1_path  = session_dir / "camera1.mjpeg"
    v2_path  = session_dir / "camera2.mjpeg"
    

    for p in (ts1_path, ts2_path, v1_path, v2_path):
        if not p.exists():
            print(f"ERROR: Required camera file missing: {p}")
            sys.exit(1)
            
    for a in (audio_time_path,):
        if not a.exists():
            print(f"ERROR: Required audio file missing: {a}")
            sys.exit(1)

    # ---- Load camera timestamps and convert to UTC -------------------------
    print("[analyze_sync] Loading camera timestamps...")
    mono1 = load_timestamps_bin(ts1_path)
    mono2 = load_timestamps_bin(ts2_path)
    print(f"  cam1: {len(mono1)} timestamps    cam2: {len(mono2)} timestamps")

    start_utc_s, start_mono_us = load_anchor(session_dir)
    utc1 = mono_us_to_utc(mono1, start_utc_s, start_mono_us)
    utc2 = mono_us_to_utc(mono2, start_utc_s, start_mono_us)
    


    # ---- Extract brightness ------------------------------------------------
    roi = tuple(args.roi) if args.roi else None
    print("[analyze_sync] Extracting brightness from camera 1...")
    bright1 = extract_brightness(v1_path, roi, args.max_frames, args.verbose)
    print("[analyze_sync] Extracting brightness from camera 2...")
    bright2 = extract_brightness(v2_path, roi, args.max_frames, args.verbose)

    n1 = min(len(mono1), len(bright1))
    n2 = min(len(mono2), len(bright2))
    utc1, bright1 = utc1[:n1], bright1[:n1]
    utc2, bright2 = utc2[:n2], bright2[:n2]
    print(f"[analyze_sync] Aligned: cam1={n1} frames  cam2={n2} frames")

    # ---- Camera figure -----------------------------------------------------
    session_label = session_dir.name
    print("[analyze_sync] Rendering camera figure...")
    fig_cam = make_camera_figure(utc1, bright1, utc2, bright2,
                                 params, session_label)


    # ---- Interactive Plotly brightness plot (hover tooltips) ---------------
    fig_html = None
    if not args.no_html:
        print("[analyze_sync] Rendering interactive brightness plot...")
        fig_html = make_brightness_html(utc1, bright1, utc2, bright2,
                                        params, session_label)

    # ---- Audio (optional) --------------------------------------------------
    audio_path = args.audio or session_dir / "audio.wav"
    fig_audio  = None
    fig_av     = None
    audio_time_path =  session_dir / "audio_timestamps.bin"
    
    # ---- Load audio timestamps and convert to UTC -------------------------
    print("[analyze_sync] Loading audio timestamps...")
    mono3 = load_timestamps_bin(audio_time_path)
    print(f"  audio: {len(mono3)} timestamps")

    start_utc_s, start_mono_us = load_anchor_audio(session_dir)
    
    # May need to change to account for audio start time....
    utc3 = mono_us_to_utc(mono3, start_utc_s, start_mono_us)


    if audio_path.exists():
        print(f"[analyze_sync] Loading audio from {audio_path}...")
        try:
            audio_samples, audio_sr = load_audio(audio_path)
            print(f"  {len(audio_samples)} samples  sr={audio_sr} Hz  "
                  f"duration={len(audio_samples)/audio_sr:.2f} s")

            # --- Determine audio start UTC (priority order) -----------------
            # 1. Explicit CLI flag
            # 2. Auto-detected t0 sidecar JSON from the recorder script
            # 3. Session start_time.txt (fallback with warning)
            if start_utc_s is not None:
                audio_start = start_utc_s
                print(f"  Audio t0 from --audio-start-utc: {audio_start:.3f} s")
            else:
                audio_start = load_audio_sidecar(session_dir, audio_path)
                if audio_start is None:
                    audio_start = start_utc_s
                    print("  [WARN] No t0 sidecar found and --audio-start-utc "
                          "not supplied. Falling back to session start_time.txt "
                          "as audio t=0.  For accurate alignment, run the "
                          "recorder with:\n"
                          f"    python3 record_audio.py --t0-sidecar "
                          f"{session_dir}/audio_t0.json -o {session_dir}/audio.wav")

            print("[analyze_sync] Rendering audio figure...")
            fig_audio = make_audio_figure(
                audio_samples, audio_sr, audio_start, params, session_label,
            )

            # --- AV comparison figure -----------------------------------
            # Build normalized video brightness and normalized audio RMS
            # on independent time bases, both relative to the same t0.
            #
            # IMPORTANT: RMS windowing smears sharp sawtooth edges, which
            # biases the measured lag by roughly the window width. We use
            # a much smaller window here (5 ms) than the main audio figure
            # (50 ms) specifically to minimize this bias for lag estimation.
            print("[analyze_sync] Rendering AV comparison figure...")
            t0_av = min(utc1[0], utc2[0], audio_start)

            rel_video_s = utc1 - t0_av           # use camera 1 as the video reference
            video_norm  = bright1 / 255.0

            WINDOW_S_AV = 0.005   # 5 ms — minimizes edge-smearing bias vs. the
                                  # 50 ms window used in the main audio figure
            rms_av, centres_av_s = compute_rms_envelope(
                audio_samples, audio_sr, WINDOW_S_AV)
            rel_audio_s = (audio_start - t0_av) + centres_av_s

            # Normalize audio RMS by its own observed peak so it's on the
            # same 0-1 scale as video brightness, regardless of --volume
            peak = float(np.max(rms_av)) if np.max(rms_av) > 0 else 1.0
            audio_norm = rms_av / peak

            fig_av = make_av_comparison_figure(
                rel_video_s, video_norm,
                rel_audio_s, audio_norm,
                params, session_label,
                rms_window_s=WINDOW_S_AV,
            )
            
            # ---- Why the charts may be misaligned -----------------------------------------------------
            print(f"start_utc_s:     {start_utc_s:.6f}")
            print(f"start_mono_us:   {start_mono_us}")
            print(f"mono1[0]:        {mono1[0]}")
            print(f"audio_start:     {audio_start:.6f}")
            print(f"utc1[0]:         {utc1[0]:.6f}")
            print(f"utc1[0]-audio:   {utc1[0] - audio_start:.3f} s")
        except Exception as e:
            print(f"  [WARN] Audio analysis failed: {e}")
    else:
        print(f"[analyze_sync] No audio file found at {audio_path} --- "
              "skipping audio figure.  Use --audio PATH to specify one.")

    # ---- Output ------------------------------------------------------------
    if args.output_prefix:
        cam_out   = Path(str(args.output_prefix) + "_camera.png")
        audio_out = Path(str(args.output_prefix) + "_audio.png")
        av_out    = Path(str(args.output_prefix) + "_av_comparison.png")
        html_out  = Path(str(args.output_prefix) + "_brightness.html")
    else:
        cam_out   = session_dir / f"{session_label}_camera.png"
        audio_out = session_dir / f"{session_label}_audio.png"
        av_out    = session_dir / f"{session_label}_av_comparison.png"
        html_out  = session_dir / f"{session_label}_brightness.html"

    if args.output_prefix:
        fig_cam.savefig(cam_out, dpi=150, bbox_inches="tight")
        print(f"[analyze_sync] Camera figure saved → {cam_out}")
        if fig_audio:
            fig_audio.savefig(audio_out, dpi=150, bbox_inches="tight")
            print(f"[analyze_sync] Audio figure saved  → {audio_out}")
        if fig_av:
            fig_av.savefig(av_out, dpi=150, bbox_inches="tight")
            print(f"[analyze_sync] AV comparison figure saved → {av_out}")
    else:
        plt.show()

    if fig_html is not None:
        fig_html.write_html(str(html_out), include_plotlyjs="cdn")
        print(f"[analyze_sync] Interactive brightness plot saved → {html_out}")
        print("  Open this file in a browser and hover over any point "
              "for exact timestamps (5 decimal places).")


if __name__ == "__main__":
    main()
