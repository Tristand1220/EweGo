# Audio Testing Scripts

Simple utilities for testing audio capture on a Raspberry Pi using ALSA's `arecord`.

## Scripts

### `local_test.sh`

Captures audio locally with a real-time VU meter display. Useful for testing audio input levels on the device itself.

```bash
./local_test.sh
```

Audio is recorded to `/dev/null` (discarded) — this is for monitoring input levels only.

### `wireless_test.sh`

Streams audio from a remote Raspberry Pi to your local machine for playback and monitoring.

```bash
./wireless_test.sh
```

You will be prompted for the Pi's address (defaults to `pi.local`). The script:
1. SSHs into the Pi
2. Captures audio using `arecord`
3. Streams it back via SSH
4. Plays it locally using `ffplay` with minimal latency settings

#### Requirements for wireless mode:
- `ffplay` (from FFmpeg) installed on the local machine
- SSH access to the Raspberry Pi
- Pi must have `arecord` available

## Usage Examples

**Test audio locally on the Pi:**
```bash
ssh pi@pi.local
./local_test.sh
```

**Stream audio from Pi to your computer:**
```bash
./wireless_test.sh
# Enter: raspberrypi.local
```

## Troubleshooting

### No audio device found
List available ALSA devices:
```bash
arecord -l
```

If your device is on a different card/device number, update the `-D hw:X,Y` parameter in the scripts.
