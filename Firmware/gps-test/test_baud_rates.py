#!/usr/bin/env python3
"""
Test different baud rates and fetch data from the GNSS module
"""

import serial
import time
import sys
from pyubx2 import UBXReader

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
        
        # Count UBX messages (look for sync bytes 0xB5 0x62)
        ubx_count = sum(1 for i in range(len(raw_data)-1) 
                       if raw_data[i] == 0xB5 and raw_data[i+1] == 0x62)
        
        # Also check for NMEA ($ or !)
        nmea_count = sum(1 for i in range(len(raw_data)) 
                        if raw_data[i:i+1] == b'$' or raw_data[i:i+1] == b'!')
        
        ser.close()
        
        return True, ubx_count, nmea_count, raw_data[:200]  # First 200 bytes
        
    except Exception as e:
        if ser:
            try:
                ser.close()
            except:
                pass
        return False, 0, 0, str(e)


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
        
        if success:
            print(f"✓ {baud:6d} baud: WORKING ({ubx_count} UBX, {nmea_count} NMEA messages)")
            working_bauds.append((baud, data))
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
    
    for baud, data in working_bauds:
        parse_and_display_data(baud, data)
    
    # Recommend the best baud rate
    print("\n" + "=" * 60)
    print("RECOMMENDATION")
    print("=" * 60)
    
    # Prefer higher baud rates for better performance
    preferred_order = [115200, 230400, 57600, 38400, 19200, 9600]
    recommended = None
    
    for preferred in preferred_order:
        for baud, _ in working_bauds:
            if baud == preferred:
                recommended = baud
                break
        if recommended:
            break
    
    if recommended:
        print(f"✓ Recommended baud rate: {recommended}")
        print(f"\nTo use this baud rate in gps_logger.py:")
        print(f"  BAUDRATE = {recommended}")
        
        if recommended == 38400:
            print(f"\nNote: 38400 is the current default in gps_logger.py")
        else:
            print(f"\nNote: You may need to reconfigure the module to use {recommended} baud")
            print(f"      Run: uv run --with pyserial baud_rate_configure.py")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
