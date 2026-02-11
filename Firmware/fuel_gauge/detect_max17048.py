#!/usr/bin/env python3
"""
Quick script to detect MAX17048 on available I2C buses
"""

import smbus2
import glob

MAX17048_ADDR = 0x36
REG_VERSION = 0x08

def check_bus(bus_num):
    """Check if MAX17048 is present on given bus"""
    try:
        bus = smbus2.SMBus(bus_num)
        # Try to read version register
        msb = bus.read_byte_data(MAX17048_ADDR, REG_VERSION)
        lsb = bus.read_byte_data(MAX17048_ADDR, REG_VERSION + 1)
        version = (msb << 8) | lsb
        bus.close()
        return True, version
    except:
        return False, None

print("Scanning for MAX17048 Fuel Gauge...")
print("=" * 60)

# Get all available I2C devices
i2c_devices = glob.glob('/dev/i2c-*')

if not i2c_devices:
    print("No I2C devices found!")
    print("You may need to enable I2C in /boot/config.txt")
    print("Add: dtparam=i2c_arm=on")
    exit(1)

print(f"Found {len(i2c_devices)} I2C bus(es): {[d.split('-')[1] for d in i2c_devices]}")
print()

found = False
for device in i2c_devices:
    bus_num = int(device.split('-')[1])
    print(f"Checking I2C bus {bus_num}...", end=' ')

    detected, version = check_bus(bus_num)
    if detected:
        print(f"✓ FOUND! Version: 0x{version:04X}")
        print(f"\nMAX17048 detected on I2C bus {bus_num} at address 0x{MAX17048_ADDR:02X}")
        print(f"\nTo run the test script:")
        print(f"  python3 max17048_test.py --bus {bus_num}")
        found = True
        break
    else:
        print("Not found")

if not found:
    print("\n✗ MAX17048 not found on any I2C bus")
    print("\nTroubleshooting:")
    print("1. Check wiring (SDA/SCL on GPIO pins 2/3)")
    print("2. Verify power to MAX17048")
    print("3. Check I2C pull-up resistors (typically 4.7kΩ)")
    print("4. Enable I2C in raspi-config if needed:")
