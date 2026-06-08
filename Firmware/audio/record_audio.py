#!/usr/bin/env python3
"""
Simple audio recorder script using ALSA (arecord)
Records audio from the default microphone and saves to a WAV file
"""

import subprocess
import sys
import signal
import time
from datetime import datetime
from pathlib import Path

# Audio recording parameters (high quality)
CHANNELS = 2  # Stereo audio
RATE = 48000  # 48kHz sampling rate (professional standard)
FORMAT = "S32_LE"  # 32-bit signed little-endian (studio quality)

def record_audio(filename=None, duration=None, device=None):
    """
    Record audio from the microphone using ALSA's arecord

    Args:
        filename: Output filename (default: auto-generated with timestamp)
        duration: Recording duration in seconds (default: record until Ctrl+C)
        device: ALSA device name (default: system default)
    """
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}.wav"

    # Build arecord command
    cmd = [
        "arecord",
        "-f", FORMAT,           # Audio format (16-bit)
        "-c", str(CHANNELS),    # Number of channels
        "-r", str(RATE),        # Sample rate
    ]

    if device:
        cmd.extend(["-D", device])

    if duration:
        cmd.extend(["-d", str(int(duration))])  # Duration in seconds

    cmd.append(filename)

    print(f"Recording to: {filename}")
    print(f"Format: {CHANNELS} channels, {RATE}Hz, 32-bit")
    if duration:
        print(f"Duration: {duration} seconds")
    else:
        print("Press Ctrl+C to stop recording")

    # Write monotonic start time so post-processing can align audio samples
    # with other sensors: sample_time_us = start_us + int(sample_idx / RATE * 1e6)
    start_us = time.monotonic_ns() // 1000
    Path(filename).with_suffix('.start_us').write_text(str(start_us))

    try:
        # Run arecord
        result = subprocess.run(cmd, check=True)
        print(f"\nRecording saved to: {filename}")
        return 0
    except subprocess.CalledProcessError as e:
        print(f"\nError during recording: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"\nRecording stopped by user")
        print(f"Recording saved to: {filename}")
        return 0

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Record audio from microphone using ALSA")
    parser.add_argument("-o", "--output", help="Output filename (default: recording_TIMESTAMP.wav)")
    parser.add_argument("-d", "--duration", type=float, help="Recording duration in seconds (default: until Ctrl+C)")
    parser.add_argument("-D", "--device", help="ALSA device name (default: system default)")

    args = parser.parse_args()

    try:
        exit_code = record_audio(filename=args.output, duration=args.duration, device=args.device)
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
