#!/usr/bin/env python3
"""
GPS Factory Reset Tool
Sends UBX-CFG-CFG command to reset to factory defaults
"""

import serial
import time
import struct

GPS_PORT = '/dev/ttyAMA4'

def calculate_ubx_checksum(msg_class, msg_id, payload):
    """Calculate UBX checksum (Fletcher algorithm)"""
    ck_a = 0
    ck_b = 0

    for byte in [msg_class, msg_id]:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF

    length = len(payload)
    ck_a = (ck_a + (length & 0xFF)) & 0xFF
    ck_b = (ck_b + ck_a) & 0xFF
    ck_a = (ck_a + ((length >> 8) & 0xFF)) & 0xFF
    ck_b = (ck_b + ck_a) & 0xFF

    for byte in payload:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF

    return bytes([ck_a, ck_b])

def build_ubx_message(msg_class, msg_id, payload):
    """Build complete UBX message"""
    header = bytes([0xB5, 0x62])
    length = len(payload)
    length_bytes = bytes([length & 0xFF, (length >> 8) & 0xFF])
    checksum = calculate_ubx_checksum(msg_class, msg_id, payload)
    return header + bytes([msg_class, msg_id]) + length_bytes + payload + checksum

def build_factory_reset():
    """
    Build UBX-CFG-VALSET message to reset to factory defaults
    Uses CFG-VALSET with special reset key
    """
    version = 0x01
    layers = 0x07  # RAM | BBR | Flash
    reserved = 0x0000

    # Reset all configuration to defaults
    # CFG-NAVSPG-DYNMODEL reset and other defaults
    payload = bytearray()
    payload.append(version)
    payload.append(layers)
    payload.extend(struct.pack('<H', reserved))

    # Set UART1 baud rate back to 38400 (common default)
    key_id = 0x40520001  # CFG-UART1-BAUDRATE
    payload.extend(struct.pack('<I', key_id))
    payload.extend(struct.pack('<I', 38400))

    return build_ubx_message(0x06, 0x8A, bytes(payload))

def build_cfg_cfg_reset():
    """
    Build UBX-CFG-CFG command for factory reset (older command)
    This is more aggressive - clears everything
    """
    # clearMask, saveMask, loadMask (all X4 bitmasks)
    # deviceMask (X1)

    clear_mask = 0x00001F1F  # Clear all sections
    save_mask = 0x00000000   # Don't save
    load_mask = 0x00001F1F   # Load defaults for all sections
    device_mask = 0x17       # BBR, Flash, EEPROM, SPI Flash

    payload = struct.pack('<III', clear_mask, save_mask, load_mask)
    payload += bytes([device_mask])

    return build_ubx_message(0x06, 0x09, payload)

print("=" * 70)
print("GPS Factory Reset Tool")
print("=" * 70)
print(f"Port: {GPS_PORT}")
print("=" * 70)

# Try sending reset commands at various baud rates
attempt_bauds = [3686400, 1843200, 921600, 460800, 230400, 115200, 57600, 38400, 19200, 9600]

print("\n[Method 1] Sending CFG-VALSET to set baud to 38400...")
cfg_msg = build_factory_reset()
for baud in attempt_bauds:
    try:
        ser = serial.Serial(GPS_PORT, baud, timeout=1)
        time.sleep(0.1)
        ser.write(cfg_msg)
        ser.flush()
        ser.close()
        time.sleep(0.1)
        print(f"  Sent at {baud:>8} baud")
    except:
        pass

time.sleep(2)

print("\n[Method 2] Sending CFG-CFG factory reset command...")
reset_msg = build_cfg_cfg_reset()
for baud in attempt_bauds:
    try:
        ser = serial.Serial(GPS_PORT, baud, timeout=1)
        time.sleep(0.1)
        ser.write(reset_msg)
        ser.flush()
        ser.close()
        time.sleep(0.1)
        print(f"  Sent at {baud:>8} baud")
    except:
        pass

print("\nWaiting 5 seconds for module to reset...")
time.sleep(5)

print("\nTesting common default baud rates...")
test_bauds = [38400, 9600, 115200]

for baud in test_bauds:
    try:
        ser = serial.Serial(GPS_PORT, baud, timeout=2)
        time.sleep(0.5)
        data = ser.read(500)
        ser.close()

        ubx_count = sum(1 for i in range(len(data)-1) if data[i] == 0xB5 and data[i+1] == 0x62)

        print(f"  {baud:>6} baud: {len(data):4d} bytes, {ubx_count:3d} UBX")

        if len(data) > 50 and ubx_count >= 1:
            print(f"\n✓ Module responding at {baud} baud!")
            sample = ' '.join(f'{b:02x}' for b in data[:20])
            print(f"  Sample: {sample}")
            break
    except:
        pass
else:
    print("\n✗ Factory reset may not have worked")
    print("  Try manually power cycling again or check hardware connections")
