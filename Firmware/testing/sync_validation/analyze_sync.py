#!/usr/bin/env python3
"""
Cross-device sensor-sync validation for the EweGo fleet.

Given two simultaneous `sensor_test.py` captures (one per device), this verifies
that the audio and IMU data are time-synchronized across devices, and quantifies
the residual offset and long-term drift.

Method
------
Each device timestamps every sample in its own CLOCK_MONOTONIC (µs). Those are
NOT comparable across devices. But chrony keeps each device's CLOCK_REALTIME
(wall clock) synchronized, so the pipeline is:

    sample -> device monotonic_us  (audio: per-block anchors in *.timestamps.csv;
                                     IMU: monotonic_us column)
           -> device wall seconds  (linear fit of clock_sync.csv: monotonic<->wall)

Both devices' signals are then placed on a common wall-clock grid and
cross-correlated. The residual lag IS the cross-device sync error.

  * Audio: GCC-PHAT in the music band (300 Hz-4 kHz). Whitening suppresses the
    narrowband mains-hum / LF-rumble that the voiceHAT picks up, so the peak is
    driven by genuine broadband content. ALWAYS confirm broadband content is
    actually captured before trusting a lag (a full-band correlation can lock
    onto grid-common 60 Hz hum and fake a near-zero alignment).
  * IMU: normalized cross-correlation of gyro |w|. Gyro |w| is rotation-frame
    invariant (same for a rigid body regardless of mount orientation); linear
    accel is position-dependent and less reliable.

Sliding windows over the whole capture give lag(t); a linear fit gives drift
(ppm). A clean linear lag(t) is a constant frequency offset between the devices'
clocks (chrony bounds the offset but leaves a small frequency residual that a
shared GPS-PPS refclock would largely remove).

Usage
-----
    uv run --no-project --with numpy --with matplotlib \
        python analyze_sync.py <capture_dir_A> <capture_dir_B> [--out results]

Each capture dir is a `sensor_test_*` directory (or any dir containing
`audio_*.wav` + `audio_*.timestamps.csv` + `clock_sync.csv` +
`imu/logs/imu_log_*.csv`). Raw captures are large and gitignored; see README
for how they were produced.
"""
import argparse
import csv
import glob
import os
import wave

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FS = 48000          # voiceHAT audio sample rate
AUDIO_BAND = (300, 4000)


# ---------------------------------------------------------------- file access
def _find(d, *patterns):
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(d, p)))
        if hits:
            return hits[0]
    raise FileNotFoundError(f"none of {patterns} under {d}")


def load_csv(path):
    with open(path) as f:
        r = csv.reader(f)
        hdr = next(r)
        rows = [row for row in r if row and row[0].strip()]
    cols = {k: i for i, k in enumerate(hdr)}
    return cols, np.array([[float(v) for v in row] for row in rows])


def mono_to_wall(clock_csv):
    """Linear map monotonic_us -> wall seconds from clock_sync.csv."""
    c, a = load_csv(clock_csv)
    mono = a[:, c["monotonic_us"]]
    wall = a[:, c["wall_time_s"]]
    m0 = mono[0]
    slope, intercept = np.polyfit(mono - m0, wall, 1)
    return lambda x: slope * (np.asarray(x, float) - m0) + intercept


# ------------------------------------------------------------------ DSP utils
def band(x, lo, hi):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / FS)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, len(x))


def _parabolic(cc, p):
    if 0 < p < len(cc) - 1:
        y0, y1, y2 = abs(cc[p - 1]), abs(cc[p]), abs(cc[p + 1])
        d = y0 - 2 * y1 + y2
        return 0.5 * (y0 - y2) / d if d else 0.0
    return 0.0


def gccphat(a, b, lo, hi):
    """Band-limited GCC-PHAT delay (ms) such that a lags b by the result."""
    a = a - a.mean()
    b = b - b.mean()
    nf = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    f = np.fft.rfftfreq(nf, 1 / FS)
    R = np.fft.rfft(a, nf) * np.conj(np.fft.rfft(b, nf))
    R[(f < lo) | (f > hi)] = 0
    mag = np.abs(R)
    R = np.where(mag > 0, R / (mag + 1e-12), 0)
    cc = np.fft.irfft(R, nf)
    cc = np.concatenate((cc[-nf // 2:], cc[:nf // 2]))
    p = int(np.argmax(np.abs(cc)))
    return (p - nf // 2 + _parabolic(cc, p)) / FS * 1000


def xcorr_lag_r(a, b, fs):
    """Normalized cross-correlation: returns (lag_ms, peak_r)."""
    a = a - a.mean()
    b = b - b.mean()
    if a.std() == 0 or b.std() == 0:
        return 0.0, 0.0
    nf = 1 << int(np.ceil(np.log2(2 * len(a))))
    cc = np.fft.irfft(np.fft.rfft(a, nf) * np.conj(np.fft.rfft(b, nf)), nf)
    cc = np.concatenate((cc[-nf // 2:], cc[:nf // 2]))
    cc /= np.sqrt((a ** 2).sum() * (b ** 2).sum())
    p = int(np.argmax(cc))
    return (p - nf // 2 + _parabolic(cc, p)) / fs * 1000, float(cc[p])


# ----------------------------------------------------------- audio (mem-light)
class AudioDev:
    """Random-access audio reader: only the frames a window needs are read."""

    def __init__(self, capture_dir):
        self.w = wave.open(_find(capture_dir, "audio_*.wav", "audio.wav"), "rb")
        self.n = self.w.getnframes()
        self.ch = self.w.getnchannels()
        c, a = load_csv(_find(capture_dir, "audio_*.timestamps.csv", "audio_ts.csv"))
        m2w = mono_to_wall(_find(capture_dir, "clock_sync.csv"))
        # coarse sample<->wall table every 50 ms (tiny, enables fast inversion)
        self.cs = np.arange(0, self.n, int(FS * 0.05))
        self.cw = m2w(np.interp(self.cs, a[:, c["sample_index"]], a[:, c["monotonic_us"]]))

    def wall_range(self):
        return self.cw[0], self.cw[-1]

    def grid(self, t0, t1):
        """Audio (channel 0) resampled onto wall-clock grid [t0, t1)."""
        n = int((t1 - t0) * FS)
        tg = t0 + np.arange(n) / FS
        s_for = np.interp(tg, self.cw, self.cs)
        lo = max(0, int(s_for[0]) - 4)
        hi = min(self.n, int(s_for[-1]) + 4)
        self.w.setpos(lo)
        seg = np.frombuffer(self.w.readframes(hi - lo), dtype=np.int32)
        seg = seg.astype(float).reshape(-1, self.ch)[:, 0]
        return tg, np.interp(s_for, np.arange(lo, hi), seg)


def audio_drift(A, B, win=8.0, step=30.0):
    """Sliding music-band GCC-PHAT lag(t). Returns (t_rel, lag_ms, W0, W1)."""
    (a0, a1), (b0, b1) = A.wall_range(), B.wall_range()
    W0, W1 = max(a0, b0) + 2, min(a1, b1) - 2
    lo, hi = AUDIO_BAND
    ts, lags = [], []
    tc = W0 + win / 2
    while tc < W1 - win / 2:
        _, xa = A.grid(tc - win / 2, tc + win / 2)
        _, xb = B.grid(tc - win / 2, tc + win / 2)
        ts.append(tc - W0)
        lags.append(gccphat(band(xa, lo, hi), band(xb, lo, hi), lo, hi))
        tc += step
    return np.array(ts), np.array(lags), W0, W1


# -------------------------------------------------------------------- imu
def imu_gyro(capture_dir):
    c, a = load_csv(_find(capture_dir, "imu/logs/imu_log_*.csv", "imu.csv"))
    wall = mono_to_wall(_find(capture_dir, "clock_sync.csv"))(a[:, c["monotonic_us"]])
    gyro = np.sqrt(a[:, c["gyro_x"]] ** 2 + a[:, c["gyro_y"]] ** 2 + a[:, c["gyro_z"]] ** 2)
    return wall, gyro


def imu_drift(A_dir, B_dir, gfs=200, win=6.0, step=3.0, min_r=0.6):
    """Sliding gyro cross-correlation over motion windows. Returns dict."""
    w8, g8 = imu_gyro(A_dir)
    w11, g11 = imu_gyro(B_dir)
    W0, W1 = max(w8[0], w11[0]), min(w8[-1], w11[-1])
    ng = int((W1 - W0) * gfs)
    t = np.arange(ng) / gfs
    r8 = np.interp(W0 + t, w8, g8)
    r11 = np.interp(W0 + t, w11, g11)
    thr = r8.mean() + 0.5 * r8.std() + 5  # window must contain real motion
    ts, lags, rs = [], [], []
    tt = 0.0
    while (tt + win) * gfs < ng:
        s, e = int(tt * gfs), int((tt + win) * gfs)
        if r8[s:e].max() > thr:
            lag, rr = xcorr_lag_r(r8[s:e], r11[s:e], gfs)
            if rr > min_r:
                ts.append(tt + win / 2)
                lags.append(lag)
                rs.append(rr)
        tt += step
    return dict(t=np.array(ts), lag=np.array(lags), r=np.array(rs),
                grid_t=t, g8=r8, g11=r11, gfs=gfs)


# ------------------------------------------------------------------- plotting
def overview_figure(A, B, A_dir, B_dir, labels, out):
    la, lb = labels
    fig, ax = plt.subplots(3, 2, figsize=(14, 13.5))
    fig.suptitle(f"EweGo cross-device sync: {la} vs {lb}", fontsize=15, fontweight="bold")

    imu = imu_drift(A_dir, B_dir)
    t, lag, r = imu["t"], imu["lag"], imu["r"]
    r8, r11, gfs = imu["g8"], imu["g11"], imu["gfs"]
    swing_c = t[np.argmax(r)] if len(t) else len(r8) / gfs / 2

    # Row 1: one swing overlay + its cross-correlation peak
    zc = int(swing_c * gfs)
    zs = slice(max(0, zc - 3 * gfs), min(len(r8), zc + 3 * gfs))
    gt = np.arange(len(r8)) / gfs
    ax[0, 0].plot(gt[zs] - gt[zs][0], r8[zs], label=la, lw=1.1)
    ax[0, 0].plot(gt[zs] - gt[zs][0], r11[zs], label=lb, lw=1.1, alpha=0.8)
    ax[0, 0].set_title("IMU gyro |ω| @100 Hz — one motion event, overlaid (6 s)")
    ax[0, 0].set_xlabel("time (s)"); ax[0, 0].set_ylabel("|ω| (deg/s)"); ax[0, 0].legend(loc="upper right")

    s = int(swing_c * gfs); e = s + int(6.0 * gfs)
    aa, bb = r8[s:e] - r8[s:e].mean(), r11[s:e] - r11[s:e].mean()
    nf = 1 << int(np.ceil(np.log2(2 * len(aa))))
    cc = np.fft.irfft(np.fft.rfft(aa, nf) * np.conj(np.fft.rfft(bb, nf)), nf)
    cc = np.concatenate((cc[-nf // 2:], cc[:nf // 2])); cc /= np.sqrt((aa ** 2).sum() * (bb ** 2).sum())
    lg = (np.arange(len(cc)) - nf // 2) / gfs * 1000; m = np.abs(lg) < 100
    ax[0, 1].plot(lg[m], cc[m]); ax[0, 1].axvline(0, color="r", ls="--", lw=1)
    ax[0, 1].set_title(f"IMU gyro cross-correlation (one event) — peak r={cc[m].max():.3f}")
    ax[0, 1].set_xlabel("lag (ms)"); ax[0, 1].set_ylabel("normalized correlation")

    # Row 2: IMU drift over the whole capture
    if len(t) > 1:
        sl, ic = np.polyfit(t, lag, 1)
        ax[1, 0].plot(t, lag, "o-", ms=4, color="C2", label="motion windows")
        ax[1, 0].plot(t, ic + sl * t, "r--", label=f"fit: {sl*1e3:+.2f} ppm")
        ax[1, 0].set_title(f"IMU gyro lag over time — drift {sl*1e3:+.2f} ppm ({len(t)} events)")
        ax[1, 0].legend(loc="upper right")
        ax[1, 1].plot(t, r, "o", ms=4, color="C3"); ax[1, 1].set_ylim(0, 1.02)
        ax[1, 1].set_title("IMU per-window correlation (lag-estimate quality)")
        ax[1, 1].set_ylabel("normalized correlation r")
    ax[1, 0].set_xlabel("time (s)"); ax[1, 0].set_ylabel("lag (ms)")
    ax[1, 1].set_xlabel("time (s)")

    # Row 3: audio envelope overlay + audio drift
    ats, alag, W0, W1 = audio_drift(A, B)
    mc = (W0 + W1) / 2
    _, xa = A.grid(mc - 2, mc + 2); _, xb = B.grid(mc - 2, mc + 2)
    xab, xbb = band(xa, *AUDIO_BAND), band(xb, *AUDIO_BAND)
    hop = int(0.02 * FS); ne = len(xab) // hop
    ea = np.sqrt([(xab[i * hop:(i + 1) * hop] ** 2).mean() for i in range(ne)])
    eb = np.sqrt([(xbb[i * hop:(i + 1) * hop] ** 2).mean() for i in range(ne)])
    te = np.arange(ne) * hop / FS
    ax[2, 0].plot(te, ea / ea.max(), label=la, lw=1.1)
    ax[2, 0].plot(te, eb / eb.max(), label=lb, lw=1.1, alpha=0.8)
    ax[2, 0].set_title("Audio loudness envelope (300 Hz–4 kHz) — overlaid (4 s)")
    ax[2, 0].set_xlabel("time (s)"); ax[2, 0].set_ylabel("normalized RMS"); ax[2, 0].legend(loc="upper right")

    keep = np.abs(alag - np.median(alag)) < 0.5
    asl, aic = np.polyfit(ats[keep], alag[keep], 1)
    ax[2, 1].plot(ats[keep], alag[keep], "o-", ms=3, label="8 s windows")
    ax[2, 1].plot(ats[keep], aic + asl * ats[keep], "r--", label=f"fit: {asl*1e3:+.3f} ppm")
    ax[2, 1].set_title(f"Audio lag over capture — drift {asl*1e3:+.3f} ppm")
    ax[2, 1].set_xlabel("time (s)"); ax[2, 1].set_ylabel("lag (ms)"); ax[2, 1].legend(loc="upper right")

    fig.tight_layout(rect=[0, 0, 1, 0.975])
    fig.savefig(out, dpi=130)
    print(f"  saved {out}")
    return dict(audio_ppm=asl * 1e3, audio_scatter_us=alag[keep].std() * 1000,
                imu_ppm=(np.polyfit(t, lag, 1)[0] * 1e3 if len(t) > 1 else float("nan")),
                imu_events=len(t))


def drift_diagnostic(A, B, labels, out):
    """First-vs-last envelope + raw-waveform zoom, showing the drift directly."""
    la, lb = labels
    (a0, a1), (b0, b1) = A.wall_range(), B.wall_range()
    W0, W1 = max(a0, b0) + 3, min(a1, b1) - 3
    lo, hi = AUDIO_BAND

    def env(x):
        xb = band(x, lo, hi); hop = int(0.01 * FS); ne = len(xb) // hop
        return np.arange(ne) * hop / FS, np.sqrt([(xb[i*hop:(i+1)*hop] ** 2).mean() for i in range(ne)])

    te, ef_a = env(A.grid(W0, W0 + 4)[1]); _, ef_b = env(B.grid(W0, W0 + 4)[1])
    _, el_a = env(A.grid(W1 - 4, W1)[1]); _, el_b = env(B.grid(W1 - 4, W1)[1])

    lag_first = gccphat(band(A.grid(W0, W0 + 8)[1], lo, hi), band(B.grid(W0, W0 + 8)[1], lo, hi), lo, hi)
    lag_last = gccphat(band(A.grid(W1 - 8, W1)[1], lo, hi), band(B.grid(W1 - 8, W1)[1], lo, hi), lo, hi)

    def zoom(t0):
        tg, xa = A.grid(t0, t0 + 0.018); _, xb = B.grid(t0, t0 + 0.018)
        return (tg - t0) * 1000, band(xa, 200, 3500), band(xb, 200, 3500)
    zsx, z_a0, z_b0 = zoom(W0 + 5)
    zex, z_a1, z_b1 = zoom(W1 - 5)

    fig, ax = plt.subplots(2, 2, figsize=(14, 8.5))
    fig.suptitle(f"Audio drift diagnostic: {la} vs {lb}", fontsize=14, fontweight="bold")
    ax[0, 0].plot(te, ef_a / ef_a.max(), label=la); ax[0, 0].plot(te, ef_b / ef_b.max(), label=lb, alpha=.8)
    ax[0, 0].set_title("Loudness envelope — FIRST 4 s"); ax[0, 0].set_xlabel("time (s)"); ax[0, 0].legend(loc="upper right")
    ax[0, 1].plot(te, el_a / el_a.max(), label=la); ax[0, 1].plot(te, el_b / el_b.max(), label=lb, alpha=.8)
    ax[0, 1].set_title("Loudness envelope — LAST 4 s (sub-ms drift ≪ 10 ms env. resolution)")
    ax[0, 1].set_xlabel("time (s)"); ax[0, 1].legend(loc="upper right")
    ax[1, 0].plot(zsx, z_a0 / np.abs(z_a0).max(), label=la); ax[1, 0].plot(zsx, z_b0 / np.abs(z_b0).max(), label=lb, alpha=.8)
    ax[1, 0].set_title(f"Raw waveform zoom — START (lag {lag_first:+.2f} ms: overlap)")
    ax[1, 0].set_xlabel("time (ms)"); ax[1, 0].legend(loc="upper right")
    ax[1, 1].plot(zex, z_a1 / np.abs(z_a1).max(), label=la); ax[1, 1].plot(zex, z_b1 / np.abs(z_b1).max(), label=lb, alpha=.8)
    ax[1, 1].set_title(f"Raw waveform zoom — END (lag {lag_last:+.2f} ms: visibly offset)")
    ax[1, 1].set_xlabel("time (ms)"); ax[1, 1].legend(loc="upper right")
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out, dpi=130)
    print(f"  saved {out}")
    return dict(lag_first_ms=lag_first, lag_last_ms=lag_last)


def main():
    ap = argparse.ArgumentParser(description="EweGo cross-device sync validation")
    ap.add_argument("dir_a", help="capture dir for device A (sensor_test_* or flat)")
    ap.add_argument("dir_b", help="capture dir for device B")
    ap.add_argument("--out", default="results", help="output dir for figures")
    ap.add_argument("--labels", default="deviceA,deviceB", help="comma-separated labels")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    labels = args.labels.split(",")

    A, B = AudioDev(args.dir_a), AudioDev(args.dir_b)
    print("Overview figure:")
    ov = overview_figure(A, B, args.dir_a, args.dir_b, labels, os.path.join(args.out, "sync_overview.png"))
    print("Drift diagnostic:")
    dd = drift_diagnostic(A, B, labels, os.path.join(args.out, "audio_drift_diagnostic.png"))

    print("\nSummary")
    print(f"  audio drift        : {ov['audio_ppm']:+.3f} ppm   (scatter {ov['audio_scatter_us']:.0f} µs)")
    print(f"  audio lag first→last: {dd['lag_first_ms']:+.3f} → {dd['lag_last_ms']:+.3f} ms")
    print(f"  IMU drift          : {ov['imu_ppm']:+.2f} ppm   ({ov['imu_events']} motion windows)")


if __name__ == "__main__":
    main()
