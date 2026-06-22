#!/bin/bash

# Default PI address
DEFAULT_PI="pi.local"

# Prompt user for PI address
read -p "Enter PI address (default: $DEFAULT_PI): " PI_ADDRESS

# Use default if no input provided
PI_ADDRESS=${PI_ADDRESS:-$DEFAULT_PI}

# Strip any accidental "user@" prefix
PI_ADDRESS=${PI_ADDRESS#*@}

echo "Connecting to: $PI_ADDRESS"

ssh user@$PI_ADDRESS "arecord -D hw:0,0 -c 2 -r 48000 -f S32_LE -t wav" | ffplay -nodisp -fflags nobuffer -flags low_delay -framedrop -
