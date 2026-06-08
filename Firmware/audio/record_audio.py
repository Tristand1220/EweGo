#!/usr/bin/env python3
"""
Audio recorder using sounddevice (PortAudio → ALSA).
Writes a WAV file and a per-block timestamps CSV with monotonic clock anchors.
"""

import csv
import queue
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd

CHANNELS = 2
RATE = 48000
BLOCKSIZE = 1024  # ~21.3 ms per callback at 48kHz


def record_audio(filename=None, duration=None, device=None):
    if filename is None:
        filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"

    ts_path = str(Path(filename).with_suffix('.timestamps.csv'))
    audio_q = queue.Queue()

    def callback(indata, frames, time_info, status):
        mono_us = time.monotonic_ns() // 1000
        wall_s = time.time()
        audio_q.put((indata.copy(), mono_us, wall_s))

    stream_kwargs = dict(
        samplerate=RATE,
        channels=CHANNELS,
        dtype='int32',
        blocksize=BLOCKSIZE,
        callback=callback,
    )
    if device is not None:
        stream_kwargs['device'] = int(device) if str(device).isdigit() else device

    print(f"Recording to: {filename}")
    print(f"Timestamps to: {ts_path}")
    print(f"Format: {CHANNELS} channels, {RATE}Hz, 32-bit")
    if duration:
        print(f"Duration: {duration} seconds")
    else:
        print("Press Ctrl+C to stop recording")

    total_frames = 0
    end_mono = (time.monotonic() + duration) if duration else None

    try:
        with wave.open(filename, 'wb') as wav_f, \
             open(ts_path, 'w', newline='') as ts_f:

            wav_f.setnchannels(CHANNELS)
            wav_f.setsampwidth(4)
            wav_f.setframerate(RATE)

            ts_writer = csv.writer(ts_f, lineterminator='\n')
            ts_writer.writerow(['monotonic_us', 'wall_time_s', 'sample_index'])

            with sd.InputStream(**stream_kwargs):
                while True:
                    if end_mono and time.monotonic() >= end_mono:
                        break
                    try:
                        chunk, mono_us, wall_s = audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    wav_f.writeframes(chunk.tobytes())
                    ts_writer.writerow([mono_us, f"{wall_s:.6f}", total_frames])
                    total_frames += BLOCKSIZE

    except KeyboardInterrupt:
        print("\nRecording stopped by user")

    print(f"Recording saved: {filename}  ({total_frames / RATE:.1f}s, {total_frames} frames)")
    print(f"Timestamps saved: {ts_path}")
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Record audio with per-block monotonic timestamps")
    parser.add_argument("-o", "--output", help="Output WAV filename (default: recording_TIMESTAMP.wav)")
    parser.add_argument("-d", "--duration", type=float, help="Recording duration in seconds (default: until Ctrl+C)")
    parser.add_argument("-D", "--device", help="ALSA device index or name substring (default: system default)")

    args = parser.parse_args()

    try:
        sys.exit(record_audio(filename=args.output, duration=args.duration, device=args.device))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
