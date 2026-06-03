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
    Plot 5 — Per-chunk RMS jitter        (chunk interval stability)
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
    import matplotlib.dates as mdates
    from matplotlib.ticker import AutoMinorLocator
except ImportError:
    print("matplotlib not installed.  pip install matplotlib")
    sys.exit(1)
 
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
        audio_freq_hz   (None if absent),
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
            return {
                "visual_freq_hz":  raw["visual"]["freq_hz"],
                "visual_waveform": raw["visual"]["waveform"],
                "audio_freq_hz":   raw.get("audio", {}).get("freq_hz"),
                "start_utc_s":     t0,
                "_raw":            raw,
            }
 
        # Legacy sine_params.json format
        t0 = _parse_utc(raw["start_utc"])
        return {
            "visual_freq_hz":  raw["freq_hz"],
            "visual_waveform": "sine",
            "audio_freq_hz":   None,
            "start_utc_s":     t0,
            "_raw":            raw,
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
 
 
def ground_truth_audio_rms(utc_times: np.ndarray, params: dict,
                           window_s: float = 0.05) -> np.ndarray:
    """
    Approximate the expected RMS envelope of a sawtooth audio signal.
    A perfect sawtooth of amplitude A has RMS = A / sqrt(3).
    We return a constant for each point (flat line = perfect signal).
    Used as a reference line on the audio RMS plot.
    """
    raw     = params.get("_raw", {})
    volume  = raw.get("audio", {}).get("volume", 0.8) if "audio" in raw else 0.8
    # RMS of a sawtooth wave with peak amplitude V is V / sqrt(3)
    expected_rms = volume / math.sqrt(3)
    return np.full_like(utc_times, expected_rms, dtype=np.float64)
 
 
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
# Shared x-axis UTC formatting
# ---------------------------------------------------------------------------
 
def _apply_elapsed_xaxis(ax, span_s: float):
    """
    Format x-axis as elapsed seconds from session start.
    Tick density scales automatically with recording length.
    """
    if span_s < 60:
        step = 5
    elif span_s < 300:
        step = 30
    elif span_s < 600:
        step = 60
    else:
        step = 120
    ax.xaxis.set_major_locator(plt.MultipleLocator(step))
    ax.xaxis.set_minor_locator(plt.MultipleLocator(step / 5))
    ax.set_xlabel("Elapsed time  (s)")
 
 
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
 
    # ---- Plot 3: Jitter ----------------------------------------------------
    ax3 = fig.add_subplot(gs[2])
    iv1 = np.diff(utc1) * 1000
    iv2 = np.diff(utc2) * 1000
    ax3.plot(rel1[1:], iv1, "-", color=CAM1_COLOR,
             alpha=0.7, linewidth=0.9, label="Camera 1")
    ax3.plot(rel2[1:], iv2, "-", color=CAM2_COLOR,
             alpha=0.7, linewidth=0.9, label="Camera 2")
    med = float(np.median(iv1))
    ax3.axhline(med, color="#ffffff", linewidth=0.8, linestyle=":",
                alpha=0.5, label=f"median {med:.1f} ms")
    ax3.set_title("Frame Interval Jitter", fontsize=10, pad=6)
    ax3.set_ylabel("Frame interval  (ms)")
    _apply_elapsed_xaxis(ax3, span_s)
    ax3.legend(loc="upper right", fontsize=8)
    ax3.annotate(
        f"cam1  std={np.std(iv1):.2f} ms  max={np.max(iv1):.1f} ms    "
        f"cam2  std={np.std(iv2):.2f} ms  max={np.max(iv2):.1f} ms",
        xy=(0.02, 0.04), xycoords="axes fraction", fontsize=8, color="#aaaaaa",
    )
 
    return fig
 
 
 
# ---------------------------------------------------------------------------
# Figure 2 — Audio sync
# ---------------------------------------------------------------------------
 
def make_audio_figure(audio_samples: np.ndarray, audio_sr: int,
                      audio_start_utc: float,
                      params: dict | None, session_label: str):
    """
    Three-panel audio analysis figure:
      Plot 4 — RMS envelope vs elapsed time  (mic + expected reference)
      Plot 5 — RMS chunk interval jitter     (stability of capture timing)
      Plot 6 — Spectrogram                   (pitch/harmonics over time)
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
 
    if params is not None and params.get("audio_freq_hz") is not None:
        utc_rms = audio_start_utc + centres_s
        ref     = ground_truth_audio_rms(utc_rms, params, WINDOW_S)
        ax4.axhline(ref[0], color=TRUTH_COLOR, linewidth=1.0, linestyle="--",
                    alpha=0.7, label=f"Expected RMS ({ref[0]:.3f})")
        ax4.annotate(
            f"audio f = {params['audio_freq_hz']:.1f} Hz",
            xy=(0.02, 0.04), xycoords="axes fraction",
            fontsize=8, color="#888888",
        )
 
    ax4.set_title("Microphone RMS Envelope vs. Elapsed Time", fontsize=10, pad=6)
    ax4.set_ylabel("RMS amplitude")
    ax4.legend(loc="upper right", fontsize=9)
    _apply_elapsed_xaxis(ax4, span_s)
 
    # ---- Plot 5: RMS chunk interval jitter ---------------------------------
    ax5 = fig.add_subplot(gs[1])
    chunk_intervals = np.diff(centres_s) * 1000
    ax5.plot(centres_s[1:], chunk_intervals, "-",
             color=AUDIO2_COLOR, alpha=0.8, linewidth=0.9,
             label="Chunk interval")
    expected_iv = WINDOW_S * 1000
    ax5.axhline(expected_iv, color="#ffffff", linewidth=0.8,
                linestyle=":", alpha=0.5, label=f"expected {expected_iv:.1f} ms")
    ax5.set_title("RMS Chunk Interval (audio capture regularity)",
                  fontsize=10, pad=6)
    ax5.set_ylabel("Interval  (ms)")
    ax5.legend(loc="upper right", fontsize=8)
    _apply_elapsed_xaxis(ax5, span_s)
    ax5.annotate(
        f"std={np.std(chunk_intervals):.3f} ms    "
        f"max_dev={np.max(np.abs(chunk_intervals - expected_iv)):.3f} ms",
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
        if params and params.get("audio_freq_hz"):
            max_freq = min(4000, params["audio_freq_hz"] * 8)
        f_mask = f <= max_freq
 
        im = ax6.pcolormesh(
            t, f[f_mask],
            10 * np.log10(Sxx[f_mask] + 1e-12),
            shading="gouraud", cmap="inferno",
        )
        plt.colorbar(im, ax=ax6, label="dB", pad=0.01)
 
        if params and params.get("audio_freq_hz"):
            fund = params["audio_freq_hz"]
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
 
    # ---- Audio (optional) --------------------------------------------------
    audio_path = args.audio or session_dir / "audio.wav"
    fig_audio  = None
 
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
            if args.audio_start_utc is not None:
                audio_start = args.audio_start_utc
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
        except Exception as e:
            print(f"  [WARN] Audio analysis failed: {e}")
    else:
        print(f"[analyze_sync] No audio file found at {audio_path} --- "
              "skipping audio figure.  Use --audio PATH to specify one.")
 
    # ---- Output ------------------------------------------------------------
    if args.output_prefix:
        cam_out   = Path(str(args.output_prefix) + "_camera.png")
        audio_out = Path(str(args.output_prefix) + "_audio.png")
        fig_cam.savefig(cam_out, dpi=150, bbox_inches="tight")
        print(f"[analyze_sync] Camera figure saved → {cam_out}")
        if fig_audio:
            fig_audio.savefig(audio_out, dpi=150, bbox_inches="tight")
            print(f"[analyze_sync] Audio figure saved  → {audio_out}")
    else:
        plt.show()
 
 
if __name__ == "__main__":
    main()
 