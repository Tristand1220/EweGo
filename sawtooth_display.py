#!/usr/bin/env python3
"""
sawtooth_display.py — Ground-truth sawtooth signal display for AV sync testing.

Drives a full-screen window whose brightness ramps linearly from 0 to 255 over
one period, then instantly resets — a classic forward sawtooth:

    B(t) = 255 * ((t * freq) % 1.0)

Simultaneously plays an audio tone (carrier pitch = --audio-freq) whose
AMPLITUDE is modulated by the exact same sawtooth envelope driving the
screen brightness:

    envelope(t) = (t * freq) % 1.0          <- same as the visual signal
    A(t)        = volume * envelope(t) * sin(2*pi*audio_freq*t)

This means the microphone's RMS envelope over time should trace the same
sawtooth ramp shape as the camera's ROI brightness — making the two
directly comparable on one set of axes. The carrier frequency is just
a "container" for the envelope; it does not itself need to match
anything visual.

On start, writes sawtooth_params.json into --output-dir so that
analyze_sync.py can reconstruct the exact ground-truth waveforms.

Usage:
    python3 sawtooth_display.py [OPTIONS]

Options:
    --freq FLOAT        Visual sawtooth frequency in Hz      (default: 1.0)
    --audio-freq FLOAT  Audio carrier pitch in Hz            (default: 440.0)
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


def build_audio_chunk(start_elapsed: float, visual_freq: float,
                      audio_freq: float, sr: int, n_frames: int,
                      volume: float) -> np.ndarray:
    """
    Generate one chunk of audio whose AMPLITUDE follows the same sawtooth
    envelope as the visual signal, carried on a fixed-pitch tone.

        envelope(t) = (t * visual_freq) % 1.0        (0.0 -> 1.0 ramp, same
                                                        as the screen brightness)
        carrier(t)  = sin(2*pi*audio_freq*t)          (fixed audible pitch)
        A(t)        = volume * envelope(t) * carrier(t)

    Args:
        start_elapsed: elapsed seconds (since recording start) at the first
                       sample of this chunk. Using absolute elapsed time
                       (rather than a running carrier phase) means the
                       envelope is always computed from true wall-clock
                       position — no per-chunk phase bookkeeping needed,
                       and the envelope can never drift relative to the
                       visual signal even over a long recording.
        visual_freq:   frequency of the sawtooth envelope (Hz) — pass the
                       SAME value used for the visual brightness so audio
                       and video stay locked to one ground truth.
        audio_freq:    carrier pitch in Hz (what you actually hear as "pitch")
        sr:            sample rate
        n_frames:      number of samples to generate
        volume:        peak volume 0.0-1.0

    Returns:
        int16 numpy array of length n_frames
    """
    # Per-sample elapsed time for this chunk
    t = start_elapsed + np.arange(n_frames) / sr

    # Envelope: identical sawtooth shape to the visual signal, 0.0 -> 1.0
    envelope = np.mod(t * visual_freq, 1.0)

    # Carrier: fixed-pitch tone, used purely so there's something to hear/RMS
    carrier = np.sin(2.0 * math.pi * audio_freq * t)

    samples_f   = envelope * carrier * volume
    samples_i16 = (samples_f * 32767).astype(np.int16)
    return samples_i16


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
            "modulation":    "amplitude",
            "envelope_waveform": "sawtooth_forward",
            "envelope_freq_hz":  freq,   # SAME as visual freq_hz — shared envelope
            "carrier_freq_hz":   audio_freq,
            "sample_rate":   AUDIO_SR,
            "channels":      AUDIO_CHANNELS,
            "volume":        volume,
            "formula": (
                "envelope(t) = (elapsed_s * envelope_freq_hz) % 1.0;  "
                "A(t) = volume * envelope(t) * sin(2*pi*carrier_freq_hz*elapsed_s)"
            ),
        },
        "start_utc":      start_utc.isoformat(),
        "start_mono_us":  start_mono_us,
        "notes": (
            "elapsed_s = wall_seconds_since_start_utc. "
            "The audio envelope is IDENTICAL in shape and frequency to the "
            "visual sawtooth (both use freq_hz), so RMS(audio) and "
            "brightness(video) should trace the same normalized ramp curve. "
            "carrier_freq_hz is only the audible pitch and carries no "
            "timing information."
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
            f"A-Carrier: {audio_freq:.1f} Hz" + ("" if audio_on else "  [OFF]"),
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
    Streams a seamless amplitude-modulated tone using pygame's Sound queue.
    The envelope tracks ABSOLUTE elapsed time since stream start, so it can
    never drift out of sync with the visual sawtooth even over a long
    recording — each chunk is computed fresh from wall-clock position
    rather than accumulated phase.
    """

    PREBUFFER_CHUNKS = 4   # chunks to pre-generate before playback starts

    def __init__(self, visual_freq: float, audio_freq: float, volume: float,
                sr: int = AUDIO_SR, chunk_frames: int = CHUNK_FRAMES):
        self.visual_freq  = visual_freq   # envelope frequency (shared w/ video)
        self.audio_freq   = audio_freq    # carrier pitch (audible tone only)
        self.volume        = volume
        self.sr            = sr
        self.chunk_frames  = chunk_frames
        self._next_elapsed = 0.0          # elapsed seconds at next chunk's first sample
        self._stop         = threading.Event()
        self._channel       = None

    def _make_sound(self) -> pygame.mixer.Sound:
        samples = build_audio_chunk(
            self._next_elapsed, self.visual_freq, self.audio_freq,
            self.sr, self.chunk_frames, self.volume,
        )
        self._next_elapsed += self.chunk_frames / self.sr
        # pygame mixer expects 2D array; duplicate mono -> stereo
        stereo = np.column_stack([samples, samples])
        return pygame.sndarray.make_sound(stereo)

    def start(self):
        self._channel = pygame.mixer.find_channel(force=True)
        sounds = [self._make_sound() for _ in range(self.PREBUFFER_CHUNKS)]
        self._channel.play(sounds[0])
        for s in sounds[1:]:
            self._channel.queue(s)
        t = threading.Thread(target=self._feeder, daemon=True)
        t.start()

    def _feeder(self):
        """Keep one chunk queued ahead at all times."""
        while not self._stop.is_set():
            if self._channel and self._channel.get_queue() is None:
                self._channel.queue(self._make_sound())
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
            visual_freq=args.freq,
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