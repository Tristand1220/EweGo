#!/usr/bin/env python3
"""
sine_display.py — Ground-truth sine signal display for camera sync testing.

Drives a full-screen window whose brightness oscillates as:
    B(t) = 127.5 * (1 + sin(2π·f·t + φ))

On start, writes sine_params.json into the target output directory so that
analyze_sync.py can reconstruct the exact ground-truth curve from wall-clock time.

Usage:
    python3 sine_display.py [OPTIONS]

Options:
    --freq FLOAT        Sine frequency in Hz           (default: 0.25)
    --duration INT      Recording duration in seconds  (default: 0, run until Ctrl+C)
    --output-dir PATH   Where to write sine_params.json (default: current dir)
    --display INT       Which display/screen to use    (default: 0)
    --no-overlay        Hide the HUD overlay
"""

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import pygame
except ImportError:
    print("pygame not installed. Install with: pip install pygame")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_TITLE = "Sync Signal — Sine Display"
TARGET_FPS   = 120          # Render at 120 Hz so brightness steps are smooth
FONT_NAME    = "monospace"
OVERLAY_ALPHA = 180         # HUD background transparency (0–255)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--freq",        type=float, default=0.25,
                   help="Sine frequency in Hz (default: 0.25 → one cycle per 4 s)")
    p.add_argument("--duration",    type=int,   default=0,
                   help="Stop after this many seconds (0 = run until Ctrl+C)")
    p.add_argument("--output-dir",  type=Path,  default=Path("."),
                   help="Directory to write sine_params.json")
    p.add_argument("--display",     type=int,   default=0,
                   help="pygame display index (0 = primary)")
    p.add_argument("--no-overlay",  action="store_true",
                   help="Hide the status HUD")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Sine parameter persistence
# ---------------------------------------------------------------------------

def write_params(output_dir: Path, freq: float, amplitude: float,
                 offset: float, start_utc: datetime, start_mono_us: int):
    """Write ground-truth parameters so the analyzer can reconstruct the signal."""
    output_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "freq_hz":          freq,
        "amplitude":        amplitude,       # half-range (127.5 for 0–255)
        "offset":           offset,          # DC offset  (127.5 for 0–255)
        "phase_rad":        0.0,             # always zero; display starts at sin(0)
        "start_utc":        start_utc.isoformat(),
        "start_mono_us":    start_mono_us,
        "notes": (
            "B(t) = offset + amplitude * sin(2*pi*freq_hz*t_seconds + phase_rad) "
            "where t_seconds is seconds elapsed since start_utc"
        )
    }
    path = output_dir / "sine_params.json"
    path.write_text(json.dumps(params, indent=2))
    print(f"[sine_display] Params written → {path}")
    return params


# ---------------------------------------------------------------------------
# HUD rendering
# ---------------------------------------------------------------------------

class HUD:
    """Lightweight status overlay rendered in the top-left corner."""

    def __init__(self, font_size: int = 18):
        self.font       = pygame.font.SysFont(FONT_NAME, font_size)
        self.small_font = pygame.font.SysFont(FONT_NAME, font_size - 4)
        self.bg_surf    = None   # rebuilt lazily when size changes

    def _bg(self, w, h):
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, OVERLAY_ALPHA))
        return surf

    def draw(self, screen, elapsed: float, freq: float,
             brightness: float, frame: int, fps: float):
        lines = [
            f"Elapsed : {elapsed:8.2f} s",
            f"Freq    : {freq:.4f} Hz  ({1/freq:.2f} s/cycle)",
            f"Phase   : {(elapsed * freq % 1.0) * 360:6.1f} °",
            f"Bright  : {brightness:5.1f} / 255",
            f"Frame   : {frame:6d}",
            f"Render  : {fps:5.1f} fps",
        ]

        padding   = 10
        line_h    = self.font.get_linesize()
        box_w     = 280
        box_h     = padding * 2 + line_h * len(lines)

        if self.bg_surf is None or self.bg_surf.get_size() != (box_w, box_h):
            self.bg_surf = self._bg(box_w, box_h)

        screen.blit(self.bg_surf, (12, 12))

        for i, line in enumerate(lines):
            surf = self.font.render(line, True, (200, 230, 200))
            screen.blit(surf, (12 + padding, 12 + padding + i * line_h))


# ---------------------------------------------------------------------------
# Main display loop
# ---------------------------------------------------------------------------

def run(args):
    # Initialise pygame
    pygame.init()
    pygame.display.set_caption(WINDOW_TITLE)

    # Pick display & go full-screen
    info   = pygame.display.Info()
    screen = pygame.display.set_mode(
        (info.current_w, info.current_h),
        pygame.FULLSCREEN | pygame.NOFRAME,
        display=args.display,
    )
    W, H = screen.get_size()
    clock = pygame.time.Clock()
    hud   = HUD() if not args.no_overlay else None

    # Capture start time pair (wall + monotonic) BEFORE writing params
    start_utc     = datetime.now(timezone.utc)
    start_mono_us = int(time.monotonic() * 1e6)

    # Derived signal constants
    freq      = args.freq
    amplitude = 127.5
    dc_offset = 127.5

    # Persist params
    write_params(args.output_dir, freq, amplitude, dc_offset,
                 start_utc, start_mono_us)

    print(f"[sine_display] Full-screen {W}×{H}  freq={freq} Hz  "
          f"period={1/freq:.2f} s")
    print("[sine_display] Press ESC or Q to quit.\n")

    frame_count  = 0
    running      = True
    render_fps   = TARGET_FPS

    while running:
        # ---- Event handling ------------------------------------------------
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

        # ---- Timing --------------------------------------------------------
        elapsed = time.monotonic() - (start_mono_us / 1e6)

        if args.duration > 0 and elapsed >= args.duration:
            print(f"[sine_display] Duration {args.duration} s reached. Exiting.")
            running = False
            break

        # ---- Signal computation --------------------------------------------
        brightness = dc_offset + amplitude * math.sin(2 * math.pi * freq * elapsed)
        b          = max(0, min(255, int(round(brightness))))
        color      = (b, b, b)

        # ---- Render --------------------------------------------------------
        screen.fill(color)

        if hud:
            hud.draw(screen, elapsed, freq, brightness, frame_count, render_fps)

        pygame.display.flip()
        frame_count += 1

        # ---- Frame rate cap ------------------------------------------------
        render_fps = clock.tick(TARGET_FPS)   # returns ms; overwrite with fps below
        render_fps = clock.get_fps()

    pygame.quit()
    print(f"[sine_display] Done. {frame_count} frames rendered.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    run(args)
