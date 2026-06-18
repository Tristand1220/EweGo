#!/usr/bin/env python3
"""
Clock-domain check — why clock_sync.csv cannot remove the inter-device drift.

Confirms that `CLOCK_MONOTONIC` (what the sensor timestamps and Python's
`time.monotonic()` use) is *rate-disciplined by chrony*, not free-running:

  * LEFT panel — within each device, wall (CLOCK_REALTIME) vs monotonic
    (CLOCK_MONOTONIC), shown as residual-from-linear. The slope deviation is
    ~0 ppm: the kernel steers BOTH clocks together with chrony's frequency
    discipline, so monotonic→wall is rate-identity (a pure offset). Only
    `CLOCK_MONOTONIC_RAW` free-runs at the bare crystal rate — and we don't use
    that one.

  * RIGHT panel — between the two devices, the audio-measured lag drift. This
    is the residual rate difference between the two chrony-disciplined clocks,
    referenced to the real world. clock_sync.csv only relates each device's two
    clocks to *each other* (both steered together), so this inter-device gap is
    common-mode invisible to it. Only a shared EXTERNAL reference reveals it:
    the audio content here, or GPS PPS (which would discipline it away).

Usage:
    uv run --no-project --with numpy --with matplotlib \
        python clock_domain_check.py <capture_A> <capture_B> --out results
"""
import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_sync import load_csv, _find, AudioDev, audio_drift  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="CLOCK_MONOTONIC vs chrony wall-clock check")
    ap.add_argument("dir_a")
    ap.add_argument("dir_b")
    ap.add_argument("--out", default="results")
    ap.add_argument("--labels", default="deviceA,deviceB")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    la, lb = args.labels.split(",")

    fig, ax = plt.subplots(1, 2, figsize=(13, 5.2))
    fig.suptitle("Why clock_sync can't remove the inter-device drift", fontweight="bold")

    # LEFT: within each device, monotonic vs wall (rate-locked => slope ~1.0)
    for tag, d in ((la, args.dir_a), (lb, args.dir_b)):
        c, a = load_csv(_find(d, "clock_sync.csv"))
        mono, wall = a[:, c["monotonic_us"]], a[:, c["wall_time_s"]]
        dm, dw = mono - mono[0], wall - wall[0]
        sl, b = np.polyfit(dm, dw, 1)
        resid = (dw - (sl * dm + b)) * 1e6        # µs
        ppm = (sl / 1e-6 - 1) * 1e6               # wall-rate vs monotonic-rate
        ax[0].plot(dm / 1e6, resid, lw=0.8, label=f"{tag} (rate dev {ppm:+.3f} ppm)")
    ax[0].set_title("WITHIN a device: wall − linear(monotonic)\n"
                    "monotonic & wall rate-locked → chrony steers both")
    ax[0].set_xlabel("time (s)"); ax[0].set_ylabel("residual (µs)"); ax[0].legend(fontsize=8)

    # RIGHT: between devices, audio-measured drift (needs external reference)
    A, B = AudioDev(args.dir_a), AudioDev(args.dir_b)
    ts, lags, _, _ = audio_drift(A, B)
    keep = np.abs(lags - np.median(lags)) < 0.5
    sl, b = np.polyfit(ts[keep], lags[keep], 1)
    ax[1].plot(ts[keep], lags[keep], "o-", ms=3, color="C3")
    ax[1].plot(ts[keep], b + sl * ts[keep], "--", color="k", label=f"{sl*1e3:+.2f} ppm residual")
    ax[1].set_title("BETWEEN devices: audio-measured lag\n(needs a shared EXTERNAL reference to see)")
    ax[1].set_xlabel("time (s)"); ax[1].set_ylabel("lag (ms)"); ax[1].legend(fontsize=9)

    out = os.path.join(args.out, "clock_adjustment.png")
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    print(f"saved {out}  (audio residual drift {sl*1e3:+.2f} ppm)")


if __name__ == "__main__":
    main()
