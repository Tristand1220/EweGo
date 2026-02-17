#!/bin/bash

# Prompt for Pi user/host with default
read -p "Enter Pi user@host [user@raspberrypi.local]: " PI_TARGET
PI_TARGET=${PI_TARGET:-user@raspberrypi.local}

echo "Streaming from $PI_TARGET..."

# Run both cameras in parallel
ssh "$PI_TARGET" "rpicam-vid -t 0 -n --camera 1 --inline --width 1280 --height 720 --framerate 15 --bitrate 2000000 --codec h264 -o - 2>/dev/null" | ffplay -f h264 -probesize 128k -fflags nobuffer -flags low_delay -framedrop -window_title 'Camera 1' -i - &
ssh "$PI_TARGET" "rpicam-vid -t 0 -n --camera 0 --inline --width 1280 --height 720 --framerate 15 --bitrate 2000000 --codec h264 -o - 2>/dev/null" | ffplay -f h264 -probesize 128k -fflags nobuffer -flags low_delay -framedrop -window_title 'Camera 0' -i - &

# Wait for both background processes
wait
echo "Streams ended."

# Double pipe/jump, using MPV
# ssh -J williamn@100.84.8.75 "$PI_TARGET" "rpicam-vid -t 0 -n --camera 1 --inline --width 1280 --height 720 --framerate 30 --bitrate 2000000 --codec h264 -o - 2>/dev/null" | mpv --no-correct-pts --fps=15 -
