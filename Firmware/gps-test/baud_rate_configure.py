#!/usr/bin/env python3
"""
GPS Baud Rate Configuration Tool for ZED-X20P
Uses UBX-CFG-VALSET (new configuration interface)
"""

import serial
import time
import sys
import struct

# Configuration
GPS_PORT = '/dev/ttyAMA4'
CURRENT_BAUD = 3686400
TARGET_BAUD = 115200


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


def build_cfg_valset_baudrate(baudrate, save_to_flash=True):
    """
    Build UBX-CFG-VALSET message for UART1 baud rate
    
    CFG-VALSET format:
    - version (U1): 0 for polling, 1 for transaction
    - layers (X1): bit 0=RAM, bit 1=BBR, bit 2=Flash
    - reserved (U2): 0
    - cfgData: key-value pairs
    
    Key ID for CFG-UART1-BAUDRATE: 0x40520001 (U4)
    """
    version = 0x01  # Transaction version
    
    # Layers: RAM (immediate) + Flash (persistent)
    if save_to_flash:
        layers = 0x07  # RAM | BBR | Flash (bits 0,1,2)
    else:
        layers = 0x01  # RAM only
    
    reserved = 0x0000
    
    # CFG-UART1-BAUDRATE key and value
    key_id = 0x40520001  # CFG-UART1-BAUDRATE
    
    # Build payload
    payload = bytearray()
    payload.append(version)
    payload.append(layers)
    payload.extend(struct.pack('<H', reserved))
    
    # Add key-value pair (key is U4, value is U4 for baudrate)
    payload.extend(struct.pack('<I', key_id))      # Key ID (little-endian)
    payload.extend(struct.pack('<I', baudrate))    # Value (little-endian)
    
    return build_ubx_message(0x06, 0x8A, bytes(payload))


def build_cfg_valset_protocols(save_to_flash=True):
    """
    Enable UBX and NMEA protocols on UART1
    Useful if protocols got disabled somehow
    """
    version = 0x01
    layers = 0x07 if save_to_flash else 0x01
    reserved = 0x0000
    
    payload = bytearray()
    payload.append(version)
    payload.append(layers)
    payload.extend(struct.pack('<H', reserved))
    
    # CFG-UART1INPROT-UBX: 0x10730001 (L - bool)
    payload.extend(struct.pack('<I', 0x10730001))
    payload.append(0x01)  # Enable
    
    # CFG-UART1INPROT-NMEA: 0x10730002 (L - bool)
    payload.extend(struct.pack('<I', 0x10730002))
    payload.append(0x01)  # Enable
    
    # CFG-UART1OUTPROT-UBX: 0x10740001 (L - bool)
    payload.extend(struct.pack('<I', 0x10740001))
    payload.append(0x01)  # Enable
    
    # CFG-UART1OUTPROT-NMEA: 0x10740002 (L - bool)
    payload.extend(struct.pack('<I', 0x10740002))
    payload.append(0x01)  # Enable
    
    return build_ubx_message(0x06, 0x8A, bytes(payload))


def open_serial_safe(port, baud, timeout=2):
    """Open serial port with retry logic"""
    for attempt in range(3):
        try:
            ser = serial.Serial(port, baud, timeout=timeout)
            time.sleep(0.3)
            return ser
        except Exception as e:
            if attempt < 2:
                time.sleep(0.5)
            else:
                raise
    return None


def read_ack(ser, timeout=1.0):
    """
    Read and parse UBX-ACK-ACK or UBX-ACK-NAK
    Returns: ('ACK', class, id) or ('NAK', class, id) or None
    """
    start_time = time.time()
    buffer = bytearray()
    
    while time.time() - start_time < timeout:
        if ser.in_waiting > 0:
            buffer.extend(ser.read(ser.in_waiting))
        
        # Look for UBX-ACK-ACK (0x05 0x01) or UBX-ACK-NAK (0x05 0x00)
        for i in range(len(buffer) - 9):  # Need at least 10 bytes
            if buffer[i] == 0xB5 and buffer[i+1] == 0x62:
                msg_class = buffer[i+2]
                msg_id = buffer[i+3]
                
                if msg_class == 0x05:  # ACK class
                    length = buffer[i+4] | (buffer[i+5] << 8)
                    if length == 2 and i + 10 <= len(buffer):
                        acked_class = buffer[i+6]
                        acked_id = buffer[i+7]
                        
                        if msg_id == 0x01:
                            return ('ACK', acked_class, acked_id)
                        elif msg_id == 0x00:
                            return ('NAK', acked_class, acked_id)
        
        time.sleep(0.05)
    
    return None


def test_baud_rate(port, baud):
    """Test if GPS responds at given baud rate"""
    ser = None
    try:
        ser = open_serial_safe(port, baud, timeout=2)
        time.sleep(0.8)
        data = ser.read(300)
        ser.close()
        time.sleep(0.5)
        
        ubx_count = sum(1 for i in range(len(data)-1) 
                       if data[i] == 0xB5 and data[i+1] == 0x62)
        
        return len(data) > 50 and ubx_count >= 1, len(data), ubx_count
    except Exception as e:
        if ser:
            try:
                ser.close()
            except:
                pass
        return False, 0, 0


def main():
    print("=" * 60)
    print("GPS Baud Rate Configuration Tool (ZED-X20P)")
    print("=" * 60)
    print(f"Port: {GPS_PORT}")
    print(f"Current baud: {CURRENT_BAUD}")
    print(f"Target baud: {TARGET_BAUD}")
    print("=" * 60)
    
    # Step 1: Verify current connection
    print(f"\n[Step 1] Verifying connection at {CURRENT_BAUD} baud...")
    time.sleep(1)
    
    success, byte_count, ubx_count = test_baud_rate(GPS_PORT, CURRENT_BAUD)
    if not success:
        print(f"✗ No GPS response at {CURRENT_BAUD} baud")
        print("  Troubleshooting:")
        print("  - Check GPS module is powered")
        print("  - Verify wiring to UART4 (GPIO 8/9)")
        print("  - Wait a few seconds and retry")
        sys.exit(1)
    
    print(f"✓ GPS responding ({byte_count} bytes, {ubx_count} UBX messages)")
    
    # Step 2: Send CFG-VALSET for baud rate
    print(f"\n[Step 2] Sending CFG-VALSET to set {TARGET_BAUD} baud...")
    
    ser = open_serial_safe(GPS_PORT, CURRENT_BAUD, timeout=2)
    
    # Clear any pending data
    ser.reset_input_buffer()
    time.sleep(0.1)
    
    cfg_msg = build_cfg_valset_baudrate(TARGET_BAUD, save_to_flash=True)
    
    print(f"  UBX-CFG-VALSET: {' '.join(f'{b:02x}' for b in cfg_msg[:16])}...")
    
    try:
        ser.write(cfg_msg)
        ser.flush()
        
        # Wait for ACK
        result = read_ack(ser, timeout=2.0)
        
        if result:
            ack_type, acked_class, acked_id = result
            if ack_type == 'ACK' and acked_class == 0x06 and acked_id == 0x8A:
                print("  ✓ Module acknowledged CFG-VALSET (0x06 0x8A)")
            elif ack_type == 'NAK':
                print(f"  ✗ Module rejected command (NAK for {acked_class:02x} {acked_id:02x})")
                ser.close()
                sys.exit(1)
        else:
            print("  ⚠ No ACK received (command may still have worked)")
    
    except Exception as e:
        print(f"  ✗ Write error: {e}")
        ser.close()
        sys.exit(1)
    
    ser.close()
    print("  ✓ Command sent")
    
    # Step 3: Verify at new baud rate
    print(f"\n[Step 3] Verifying at {TARGET_BAUD} baud...")
    print("  Waiting 2 seconds for module to apply settings...")
    time.sleep(2)
    
    success, byte_count, ubx_count = test_baud_rate(GPS_PORT, TARGET_BAUD)
    
    if success:
        print(f"✓ GPS responding at {TARGET_BAUD} baud ({byte_count} bytes, {ubx_count} UBX)")
        print("\n" + "=" * 60)
        print("SUCCESS!")
        print("=" * 60)
        print(f"\nGPS module now configured for {TARGET_BAUD} baud")
        print("Setting saved to RAM + BBR + Flash (persists across power cycles)")
        print("\nNext steps:")
        print(f"  1. Update gps_logger.py: BAUDRATE = {TARGET_BAUD}")
        print(f"  2. Run your GPS application")
        return 0
    else:
        print(f"✗ No response at {TARGET_BAUD} baud")
        print("\n" + "=" * 60)
        print("CONFIGURATION MAY HAVE FAILED")
        print("=" * 60)
        
        # Check if still at old rate
        print(f"\nChecking if still at {CURRENT_BAUD} baud...")
        time.sleep(0.5)
        success, byte_count, ubx_count = test_baud_rate(GPS_PORT, CURRENT_BAUD)
        
        if success:
            print(f"✓ GPS still at {CURRENT_BAUD} baud (unchanged)")
            print("\nTroubleshooting:")
            print("  - Module may not support this baud rate")
            print("  - Try a different baud rate (57600, 230400)")
            print("  - Check datasheet for supported rates")
        else:
            print("✗ No response at any baud rate - check GPS connections")
        
        return 1


if __name__ == '__main__':
    sys.exit(main())
