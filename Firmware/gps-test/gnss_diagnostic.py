#!/usr/bin/env python3
"""
GNSS Module Diagnostic Tool
Tests baud rates and attempts communication with the module
"""

import serial
import time
import sys
import os

def test_baud_rate(port, baud, duration=3):
    """Test if GNSS module responds at given baud rate"""
    ser = None
    try:
        ser = serial.Serial(port, baud, timeout=1)
        time.sleep(0.3)  # Stabilize
        
        # Clear buffers
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        
        # Collect data over the duration
        all_data = bytearray()
        start = time.time()
        while time.time() - start < duration:
            chunk = ser.read(1024)
            if chunk:
                all_data.extend(chunk)
            else:
                time.sleep(0.1)
        
        ser.close()
        
        data_len = len(all_data)
        
        # Analyze data
        ubx_count = sum(1 for i in range(data_len-1) 
                       if all_data[i] == 0xB5 and all_data[i+1] == 0x62)
        nmea_count = sum(1 for i in range(data_len) 
                        if all_data[i:i+1] == b'$')
        
        return True, data_len, ubx_count, nmea_count, bytes(all_data[:100])
        
    except Exception as e:
        if ser:
            try:
                ser.close()
            except:
                pass
        return False, 0, 0, 0, str(e)


def send_ubx_poll(ser, msg_class, msg_id):
    """Send a UBX poll message and wait for response"""
    # Build UBX message
    header = bytes([0xB5, 0x62])
    payload = bytes([])  # Poll has no payload
    length = bytes([0x00, 0x00])
    
    # Calculate checksum
    ck_a = 0
    ck_b = 0
    for byte in [msg_class, msg_id, 0x00, 0x00]:
        ck_a = (ck_a + byte) & 0xFF
        ck_b = (ck_b + ck_a) & 0xFF
    
    checksum = bytes([ck_a, ck_b])
    msg = header + bytes([msg_class, msg_id]) + length + checksum
    
    ser.write(msg)
    ser.flush()
    time.sleep(0.5)
    return ser.read(100)


def poll_module_info(port, baud):
    """Try to poll module version/info via UBX"""
    try:
        ser = serial.Serial(port, baud, timeout=2)
        time.sleep(0.3)
        ser.reset_input_buffer()
        
        # Poll NAV-PVT (position)
        print(f"    Polling NAV-PVT...")
        response = send_ubx_poll(ser, 0x01, 0x07)
        
        if response:
            ubx_found = b'\xb5\x62' in response
            print(f"    Response: {len(response)} bytes, UBX sync: {ubx_found}")
            if response:
                hex_preview = ' '.join(f'{b:02x}' for b in response[:20])
                print(f"    Hex: {hex_preview}")
        
        ser.close()
        return len(response) > 0
    except Exception as e:
        print(f"    Error: {e}")
        return False


def main():
    print("=" * 70)
    print(" GNSS Module Diagnostic Tool")
    print("=" * 70)
    
    port = '/dev/ttyAMA4'
    baud_rates = [9600, 19200, 38400, 57600, 115200, 230400, 460800]
    
    print(f"\nPort: {port}")
    print(f"Testing baud rates: {baud_rates}")
    print("-" * 70)
    
    # Check if port exists
    if not os.path.exists(port):
        print(f"✗ ERROR: Port {port} does not exist!")
        print(f"  Available tty devices:")
        for dev in os.listdir('/dev'):
            if dev.startswith('tty'):
                print(f"    /dev/{dev}")
        return 1
    
    print(f"✓ Port {port} exists")
    
    # Check permissions
    try:
        with open(port, 'rb') as f:
            pass
        print(f"✓ Port is readable")
    except PermissionError:
        print(f"✗ ERROR: No permission to read {port}")
        print(f"  Add user to 'dialout' group: sudo usermod -a -G dialout $USER")
        return 1
    
    # Test each baud rate
    print(f"\n{'Baud Rate':<12} {'Status':<10} {'Bytes':<8} {'UBX':<6} {'NMEA':<6} {'Sample Data'}")
    print("-" * 70)
    
    working_rates = []
    
    for baud in baud_rates:
        success, data_len, ubx_count, nmea_count, sample = test_baud_rate(port, baud)
        
        if success and data_len > 0:
            status = "✓ WORKING"
            working_rates.append(baud)
            sample_str = ' '.join(f'{b:02x}' for b in sample[:12])
        elif success:
            status = "silent"
            sample_str = "(no data)"
        else:
            status = "✗ ERROR"
            sample_str = str(sample)[:24]
        
        print(f"{baud:<12} {status:<10} {data_len:<8} {ubx_count:<6} {nmea_count:<6} {sample_str}")
    
    # Summary
    print("\n" + "=" * 70)
    print(" SUMMARY")
    print("=" * 70)
    
    if not working_rates:
        print("✗ No GNSS module detected at any baud rate")
        print("\nTroubleshooting steps:")
        print("1. Check power LED on the GNSS module")
        print("2. Verify TX/RX wiring (GPS TX → Pi RX, GPS RX → Pi TX)")
        print("3. Check that UART4 is enabled in /boot/firmware/config.txt:")
        print("      dtoverlay=uart4")
        print("4. Try a hardware reset of the GNSS module")
        print("5. Verify the module is outputting data (may need config)")
        return 1
    
    print(f"✓ Module responding at: {', '.join(str(b) for b in working_rates)} baud")
    
    # Recommend best rate
    preferred = [115200, 230400, 57600, 38400, 19200, 9600]
    recommended = None
    for p in preferred:
        if p in working_rates:
            recommended = p
            break
    
    if recommended:
        print(f"\n✓ Recommended baud rate: {recommended}")
        print(f"\nTo log data, run:")
        print(f"  uv run --with pyserial --with pyubx2 gps_logger.py")
        print(f"\nMake sure BAUDRATE = {recommended} in gps_logger.py")
        
        # Try to get more detailed info
        print(f"\nAttempting to poll module info at {recommended} baud...")
        poll_module_info(port, recommended)
    
    return 0


if __name__ == '__main__':
    sys.exit(main())
