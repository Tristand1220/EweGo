#!/usr/bin/env python3
"""
Test different baud rates and fetch data from the GNSS module
"""

import io
import serial
import time
import sys
from pyubx2 import UBXReader, UBX_PROTOCOL, NMEA_PROTOCOL

# Configuration
GPS_PORT = '/dev/ttyAMA4'
BAUD_RATES_TO_TEST = [9600, 19200, 38400, 57600, 115200, 230400, 460800]

def test_baud_rate(port, baud):
    """Test if GPS responds at given baud rate and return sample data"""
    ser = None
    try:
        ser = serial.Serial(port, baud, timeout=3)
        time.sleep(0.5)  # Wait for port to stabilize
        
        # Clear buffer
        ser.reset_input_buffer()
        
        # Read data for a few seconds
        print(f"  Reading data at {baud} baud...")
        time.sleep(2)
        
        raw_data = ser.read(1000)

        if len(raw_data) == 0:
            return False, 0, 0, None

        # Only checksum-valid messages count — at the wrong baud rate the
        # port still delivers bytes (line noise), which previously produced
        # false "WORKING" results and a bogus recommendation.
        ubx_count, nmea_count = count_valid_messages(raw_data)

        ser.close()

        return True, ubx_count, nmea_count, raw_data[:200]  # First 200 bytes
        
    except Exception as e:
        if ser:
            try:
                ser.close()
            except:
                pass
        return False, 0, 0, str(e)


def count_valid_messages(raw_data):
    """Count checksum-valid UBX and NMEA messages in a byte buffer"""
    ubx_count = 0
    nmea_count = 0
    ubr = UBXReader(io.BytesIO(raw_data),
                    protfilter=UBX_PROTOCOL | NMEA_PROTOCOL,
                    quitonerror=0)  # skip junk instead of raising
    while True:
        try:
            raw_msg, parsed_msg = ubr.read()
        except Exception:
            break
        if raw_msg is None:
            break
        if parsed_msg is None:
            continue
        if raw_msg[:2] == b'\xb5\x62':
            ubx_count += 1
        elif raw_msg[:1] == b'$':
            nmea_count += 1
    return ubx_count, nmea_count


def parse_and_display_data(baud, raw_data):
    """Parse and display GPS data at the given baud rate"""
    print(f"\n{'='*60}")
    print(f"Testing {baud} baud - RAW DATA SAMPLE:")
    print(f"{'='*60}")
    
    # Show hex dump of first 100 bytes
    print("Hex dump (first 100 bytes):")
    for i in range(0, min(100, len(raw_data)), 16):
        hex_str = ' '.join(f'{b:02x}' for b in raw_data[i:i+16])
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw_data[i:i+16])
        print(f"  {i:04x}: {hex_str:<48} {ascii_str}")
    
    # Try to parse with UBXReader
    print(f"\n--- Parsed UBX Messages at {baud} baud ---")
    try:
        # Create a temporary buffer to parse
        import io
        buffer = io.BytesIO(raw_data)
        ubr = UBXReader(buffer)
        
        msg_count = 0
        nav_pvt_found = False
        
        while True:
            try:
                (raw_msg, parsed_msg) = ubr.read()
                if not raw_msg:
                    break
                    
                msg_count += 1
                
                if parsed_msg:
                    identity = parsed_msg.identity
                    print(f"  [{msg_count}] {identity}", end="")
                    
                    # Special handling for NAV-PVT (position, velocity, time)
                    if identity == 'NAV-PVT':
                        nav_pvt_found = True
                        fix_types = {0: "NO FIX", 1: "DR", 2: "2D", 3: "3D", 4: "GNSS+DR", 5: "TIME"}
                        fix_str = fix_types.get(parsed_msg.fixType, "UNKNOWN")
                        print(f"\n      Fix: {fix_str} | Sats: {parsed_msg.numSV}")
                        print(f"      Lat: {parsed_msg.lat:.7f}° | Lon: {parsed_msg.lon:.7f}°")
                        print(f"      Height: {parsed_msg.height/1000:.2f}m")
                        print(f"      Time: {parsed_msg.year}-{parsed_msg.month:02d}-{parsed_msg.day:02d} "
                              f"{parsed_msg.hour:02d}:{parsed_msg.min:02d}:{parsed_msg.second:02d}")
                        if hasattr(parsed_msg, 'carrSoln'):
                            carr_types = {0: "None", 1: "Float", 2: "Fixed"}
                            print(f"      Carrier Solution: {carr_types.get(parsed_msg.carrSoln, 'Unknown')}")
                    else:
                        print()
                        
                    # Stop after parsing a reasonable number of messages
                    if msg_count >= 10:
                        break
                else:
                    print(f"  [{msg_count}] (unparsed/raw)")
                    
            except Exception as e:
                break
        
        if msg_count == 0:
            print("  No parseable UBX messages found in sample")
        elif not nav_pvt_found:
            print("\n  (NAV-PVT not in this sample, but other UBX messages detected)")
            
    except Exception as e:
        print(f"  Parse error: {e}")


def main():
    print("=" * 60)
    print("GNSS Module Baud Rate Tester")
    print("=" * 60)
    print(f"Port: {GPS_PORT}")
    print(f"Testing baud rates: {BAUD_RATES_TO_TEST}")
    print("=" * 60)
    
    working_bauds = []
    
    # Phase 1: Quick scan to find which baud rates work
    print("\n--- Phase 1: Scanning baud rates ---\n")
    
    for baud in BAUD_RATES_TO_TEST:
        success, ubx_count, nmea_count, data = test_baud_rate(GPS_PORT, baud)

        if success and (ubx_count + nmea_count) > 0:
            print(f"✓ {baud:6d} baud: WORKING ({ubx_count} UBX, {nmea_count} NMEA messages)")
            working_bauds.append((baud, data, ubx_count + nmea_count))
        elif success:
            print(f"✗ {baud:6d} baud: data received but no valid messages (noise — wrong rate)")
        else:
            print(f"✗ {baud:6d} baud: No response")
    
    print("\n" + "=" * 60)
    print(f"Summary: {len(working_bauds)} working baud rate(s) found")
    print("=" * 60)
    
    if not working_bauds:
        print("\n✗ No GNSS module detected at any baud rate!")
        print("Troubleshooting:")
        print("  - Check GPS module is powered on")
        print("  - Verify wiring to UART4 (GPIO 8/9 on Pi)")
        print("  - Ensure port /dev/ttyAMA4 exists")
        return 1
    
    # Phase 2: Parse and display data from each working baud rate
    print("\n--- Phase 2: Parsing data from working baud rates ---\n")
    
    for baud, data, _ in working_bauds:
        parse_and_display_data(baud, data)

    # Recommend the rate that produced the most valid messages
    # (tiebreak: higher baud)
    print("\n" + "=" * 60)
    print("RECOMMENDATION")
    print("=" * 60)

    recommended = max(working_bauds, key=lambda w: (w[2], w[0]))[0]
    print(f"✓ Recommended baud rate: {recommended}")
    print(f"\nTo use this baud rate in gps_logger.py:")
    print(f"  uv run gps_logger.py --baud {recommended}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
