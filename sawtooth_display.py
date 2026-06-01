#!/usr/bin/env python3
"""
sawtooth_display.py — Ground-truth sawtooth signal display for AV sync testing.

Drives a full-screen window whose brightness ramps linearly from 0 to 255 over
one period, then instantly resets — a classic forward sawtooth:

    B(t) = 255 * ((t * freq) % 1.0)

Simultaneously plays a sawtooth audio tone at a configurable pitch frequency
so that microphone capture can be analysed in the same way as camera capture.

On start, writes sawtooth_params.json into --output-dir so that
analyze_sync.py can reconstruct the exact ground-truth waveforms.

Usage:
    python3 sawtooth_display.py [OPTIONS]

Options:
    --freq FLOAT        Visual sawtooth frequency in Hz      (default: 1.0)
    --audio-freq FLOAT  Audio sawtooth pitch in Hz           (default: 440.0)
    --duration INT      Stop after N seconds  (0 = run until Q/ESC/Ctrl+C)
    --output-dir PATH   Directory for sawtooth_params.json   (default: .)
    --display INT       pygame display index                  (default: 0)
    --no-overlay        Hide the HUD status panel
    --no-audio          Disable audio output (visual-only mode)
    --volume FLOAT      Audio volume 0.0–1.0                 (default: 0.8)
"""

import argparse
import json
import math
import sys
import time
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
except ImportError:
    print("numpy not installed.  pip install numpy")
    sys.exit(1)

try:
    import pygame
    import pygame.sndarray
except ImportError:
    print("pygame not installed.  pip install pygame")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WINDOW_TITLE   = "Sync Signal — Sawtooth Display"
TARGET_FPS     = 120          # Render rate — smooth brightness steps
AUDIO_SR       = 44100        # Sample rate for audio generation
AUDIO_CHANNELS = 1
CHUNK_FRAMES   = 1024         # Audio buffer size in samples
FONT_NAME      = "monospace"
OVERLAY_ALPHA  = 180


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--freq",       type=float, default=1.0,
                   help="Visual sawtooth frequency Hz (default: 1.0)")
    p.add_argument("--audio-freq", type=float, default=440.0,
                   help="Audio sawtooth pitch Hz (default: 440.0)")
    p.add_argument("--duration",   type=int,   default=0,
                   help="Stop after N seconds (0 = indefinite)")
    p.add_argument("--output-dir", type=Path,  default=Path("."),
                   help="Where to write sawtooth_params.json")
    p.add_argument("--display",    type=int,   default=0,
                   help="pygame display index (0 = primary)")
    p.add_argument("--no-overlay", action="store_true",
                   help="Hide the HUD overlay")
    p.add_argument("--no-audio",   action="store_true",
                   help="Disable audio output")
    p.add_argument("--volume",     type=float, default=0.8,
                   help="Audio volume 0.0–1.0 (default: 0.8)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Sawtooth maths
# ---------------------------------------------------------------------------

def visual_sawtooth(elapsed: float, freq: float) -> float:
    """
    Returns brightness 0.0–255.0 for a forward sawtooth:
        B(t) = 255 * ((t * freq) % 1.0)
    Instant drop at each period boundary.
    """
    return 255.0 * math.fmod(elapsed * freq, 1.0)


def build_audio_sawtooth_chunk(phase: float, audio_freq: float,
                               sr: int, n_frames: int,
                               volume: float) -> tuple[np.ndarray, float]:
    """
    Generate one chunk of a sawtooth audio waveform as int16 samples.
    Returns (samples_int16, next_phase).

    Sawtooth: amplitude ramps linearly from -1 to +1 over one audio period,
    then wraps. Phase is tracked continuously so chunks stitch seamlessly.
    """
    # Phase increment per sample
    phase_inc = audio_freq / sr

    # Phase array for this chunk
    phases = (phase + np.arange(n_frames) * phase_inc) % 1.0

    # Sawtooth: map phase [0,1) -> amplitude [-1, +1)
    samples_f = (phases * 2.0 - 1.0) * volume

    # Convert to int16
    samples_i16 = (samples_f * 32767).astype(np.int16)

    next_phase = (phase + n_frames * phase_inc) % 1.0
    return samples_i16, next_phase


# ---------------------------------------------------------------------------
# Parameter persistence
# ---------------------------------------------------------------------------

def write_params(output_dir: Path, freq: float, audio_freq: float,
                 volume: float, start_utc: datetime,
                 start_mono_us: int) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    params = {
        "visual": {
            "waveform":      "sawtooth_forward",
            "freq_hz":       freq,
            "min_brightness": 0,
            "max_brightness": 255,
            "formula":       "B(t) = 255 * ((elapsed_s * freq_hz) % 1.0)",
        },
        "audio": {
            "waveform":      "sawtooth_forward",
            "freq_hz":       audio_freq,
            "sample_rate":   AUDIO_SR,
            "channels":      AUDIO_CHANNELS,
            "volume":        volume,
            "formula":       "A(t) = volume * (2 * ((elapsed_s * freq_hz) % 1.0) - 1)",
        },
        "start_utc":      start_utc.isoformat(),
        "start_mono_us":  start_mono_us,
        "notes": (
            "elapsed_s = wall_seconds_since_start_utc. "
            "Visual and audio sawtooth share start_utc as t=0. "
            "Audio formula gives amplitude in [-volume, +volume]."
        ),
    }
    path = output_dir / "sawtooth_params.json"
    path.write_text(json.dumps(params, indent=2))
    print(f"[sawtooth_display] Params written → {path}")
    return params


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------

class HUD:
    def __init__(self, font_size: int = 18):
        self.font    = pygame.font.SysFont(FONT_NAME, font_size)
        self.bg_surf = None

    def _bg(self, w, h):
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        surf.fill((0, 0, 0, OVERLAY_ALPHA))
        return surf

    def draw(self, screen, elapsed: float, freq: float, audio_freq: float,
             brightness: float, frame: int, fps: float, audio_on: bool):
        phase_pct = math.fmod(elapsed * freq, 1.0) * 100.0
        lines = [
            f"Elapsed   : {elapsed:8.2f} s",
            f"V-Freq    : {freq:.4f} Hz  ({1/freq:.2f} s/cycle)",
            f"V-Phase   : {phase_pct:5.1f} %",
            f"Brightness: {brightness:5.1f} / 255",
            f"A-Freq    : {audio_freq:.1f} Hz" + ("" if audio_on else "  [OFF]"),
            f"Frame     : {frame:6d}",
            f"Render    : {fps:5.1f} fps",
        ]
        padding = 10
        line_h  = self.font.get_linesize()
        box_w   = 300
        box_h   = padding * 2 + line_h * len(lines)
        if self.bg_surf is None or self.bg_surf.get_size() != (box_w, box_h):
            self.bg_surf = self._bg(box_w, box_h)
        screen.blit(self.bg_surf, (12, 12))
        for i, line in enumerate(lines):
            col  = (180, 230, 180) if audio_on else (230, 180, 180)
            surf = self.font.render(line, True, col)
            screen.blit(surf, (12 + padding, 12 + padding + i * line_h))


# ---------------------------------------------------------------------------
# Continuous audio stream via pygame mixer
# ---------------------------------------------------------------------------

class SawtoothAudioStream:
    """
    Streams a seamless sawtooth tone using pygame's Sound object queue.
    Generates chunks slightly ahead of playback to avoid gaps.
    """

    PREBUFFER_CHUNKS = 4   # chunks to pre-generate before playback starts

    def __init__(self, audio_freq: float, volume: float, sr: int = AUDIO_SR,
                 chunk_frames: int = CHUNK_FRAMES):
        self.audio_freq   = audio_freq
        self.volume       = volume
        self.sr           = sr
        self.chunk_frames = chunk_frames
        self.phase        = 0.0
        self._stop        = threading.Event()
        self._channel     = None

    def _make_sound(self) -> tuple[pygame.mixer.Sound, float]:
        samples, next_phase = build_audio_sawtooth_chunk(
            self.phase, self.audio_freq, self.sr,
            self.chunk_frames, self.volume,
        )
        # pygame mixer expects 2D array for mono: shape (N, 1)  or (N,2) stereo
        # We initialised mixer as stereo (required by some drivers), so duplicate
        stereo = np.column_stack([samples, samples])
        sound  = pygame.sndarray.make_sound(stereo)
        return sound, next_phase

    def start(self):
        self._channel = pygame.mixer.find_channel(force=True)
        # Pre-fill buffer
        sounds = []
        for _ in range(self.PREBUFFER_CHUNKS):
            s, self.phase = self._make_sound()
            sounds.append(s)
        # Queue them all
        self._channel.play(sounds[0])
        for s in sounds[1:]:
            self._channel.queue(s)
        # Background thread keeps the queue topped up
        t = threading.Thread(target=self._feeder, daemon=True)
        t.start()

    def _feeder(self):
        """Keep one chunk queued ahead at all times."""
        while not self._stop.is_set():
            # Only queue a new chunk when the channel has finished the current
            # and needs the next one (get_queue returns None when slot is free)
            if self._channel and self._channel.get_queue() is None:
                s, self.phase = self._make_sound()
                self._channel.queue(s)
            time.sleep(self.chunk_frames / self.sr / 2)

    def stop(self):
        self._stop.set()
        if self._channel:
            self._channel.stop()


# ---------------------------------------------------------------------------
# Main display loop
# ---------------------------------------------------------------------------

def run(args):
    # --- pygame init --------------------------------------------------------
    pygame.init()

    # Mixer must be initialised before display for some drivers.
    # Use stereo int16 at 44100 Hz; chunk size drives latency.
    if not args.no_audio:
        try:
            pygame.mixer.pre_init(
                frequency=AUDIO_SR,
                size=-16,           # signed 16-bit
                channels=2,         # stereo (most drivers require it)
                buffer=CHUNK_FRAMES,
            )
            pygame.mixer.init()
            audio_ok = True
        except Exception as e:
            print(f"[sawtooth_display] WARNING: audio init failed: {e}")
            audio_ok = False
    else:
        audio_ok = False

    pygame.display.set_caption(WINDOW_TITLE)
    info   = pygame.display.Info()
    screen = pygame.display.set_mode(
        (info.current_w, info.current_h),
        pygame.FULLSCREEN | pygame.NOFRAME,
        display=args.display,
    )
    clock = pygame.time.Clock()
    hud   = HUD() if not args.no_overlay else None

    # --- Anchor times -------------------------------------------------------
    start_utc     = datetime.now(timezone.utc)
    start_mono_us = int(time.monotonic() * 1e6)

    # --- Write params -------------------------------------------------------
    write_params(
        args.output_dir,
        freq=args.freq,
        audio_freq=args.audio_freq,
        volume=args.volume,
        start_utc=start_utc,
        start_mono_us=start_mono_us,
    )

    print(f"[sawtooth_display] Visual: {args.freq} Hz sawtooth  "
          f"(period {1/args.freq:.2f} s, 0→255 ramp)")
    if audio_ok:
        print(f"[sawtooth_display] Audio:  {args.audio_freq} Hz sawtooth tone  "
              f"volume={args.volume}")
    print("[sawtooth_display] Press Q or ESC to quit.\n")

    # --- Start audio stream -------------------------------------------------
    audio_stream = None
    if audio_ok:
        audio_stream = SawtoothAudioStream(
            audio_freq=args.audio_freq,
            volume=args.volume,
            sr=AUDIO_SR,
            chunk_frames=CHUNK_FRAMES,
        )
        audio_stream.start()

    # --- Main render loop ---------------------------------------------------
    frame_count = 0
    render_fps  = TARGET_FPS
    running     = True

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

        elapsed = time.monotonic() - (start_mono_us / 1e6)

        if args.duration > 0 and elapsed >= args.duration:
            print(f"[sawtooth_display] Duration {args.duration} s reached.")
            running = False
            break

        # Visual sawtooth brightness
        brightness = visual_sawtooth(elapsed, args.freq)
        b          = max(0, min(255, int(brightness)))
        screen.fill((b, b, b))

        if hud:
            hud.draw(screen, elapsed, args.freq, args.audio_freq,
                     brightness, frame_count, render_fps, audio_ok)

        pygame.display.flip()
        frame_count += 1
        render_fps = clock.tick(TARGET_FPS)
        render_fps = clock.get_fps()

    # --- Cleanup ------------------------------------------------------------
    if audio_stream:
        audio_stream.stop()
    pygame.quit()
    print(f"[sawtooth_display] Done. {frame_count} frames rendered.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = parse_args()
    run(args)
