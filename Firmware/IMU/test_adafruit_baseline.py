import sys
from types import ModuleType

# --- COMPATIBILITY PATCH START ---
# This fixes the "NameError: name 'DigitalInOut' is not defined" on Python 3.13
# We pre-load a fake 'digitalio' module so the library doesn't crash when importing.
try:
    import digitalio
except ImportError:
    print("Applying digitalio patch for Python 3.13 compatibility...")
    
    # Create a mock module
    mock_digitalio = ModuleType("digitalio")
    
    # Create a mock class for DigitalInOut
    class MockDigitalInOut:
        def __init__(self, pin): pass
        def switch_to_output(self, value=False, drive_mode=None): pass
        def switch_to_input(self, pull=None): pass
        @property
        def value(self): return False
        @value.setter
        def value(self, val): pass
        @property
        def direction(self): return None
        @property
        def pull(self): return None

    # Assign mocks to the module
    mock_digitalio.DigitalInOut = MockDigitalInOut
    mock_digitalio.Direction = None
    mock_digitalio.Pull = None
    
    # Inject into system modules
    sys.modules["digitalio"] = mock_digitalio
# --- COMPATIBILITY PATCH END ---

import time
import serial

# Check if the Adafruit library is installed
try:
    import adafruit_bno055
except ImportError:
    print("Error: Library not found.")
    print("Please run: pip install adafruit-circuitpython-bno055 pyserial adafruit-blinka")
    sys.exit(1)

# --- DEBUG WRAPPER START ---
class DebugSerial:
    """
    Wraps the serial connection to print raw TX/RX bytes for debugging.
    """
    def __init__(self, inner_serial):
        self._inner = inner_serial

    def __getattr__(self, name):
        # Pass unknown attributes (like .flush, .in_waiting) to the real serial object
        return getattr(self._inner, name)

    def write(self, data):
        # Print what we are sending (TX)
        hex_data = ' '.join([f"{b:02X}" for b in data])
        print(f"[TX] -> {hex_data}")
        return self._inner.write(data)

    def read(self, size=1):
        # Read from real serial
        data = self._inner.read(size)
        
        # Print what we received (RX)
        if data:
            hex_data = ' '.join([f"{b:02X}" for b in data])
            print(f"[RX] <- {hex_data}")
        else:
            # Don't spam if nothing is received, unless debugging timeout issues
            # print("[RX] <- (Timeout/Empty)")
            pass
            
        return data
# --- DEBUG WRAPPER END ---

def main():
    print("--------------------------------------")
    print("   BNO055 UART Test Script (Pi)       ")
    print("--------------------------------------")
    print("Targeting UART 5...")
    print("User specified pins: 31 and 28")

    # 1. Setup Serial Connection
    serial_port = "/dev/ttyAMA5" 
    print(f"Opening {serial_port}...")

    try:
        raw_uart = serial.Serial(serial_port, 115200, timeout=0.1)
        # Give the port a moment to stabilize
        time.sleep(1.0)
        
        # WRAP the serial object in our debugger
        uart = DebugSerial(raw_uart)
        
    except serial.SerialException as e:
        print(f"\n[!] Error opening serial port: {e}")
        print(f"    - Does {serial_port} exist? (Run 'ls /dev/ttyAMA*')")
        print("    - Did you add 'dtoverlay=uart5' to config.txt?")
        print("    - Did you reboot?")
        sys.exit(1)

    # 2. Initialize the BNO055 Sensor with Retries
    print("Connecting to BNO055...")
    sensor = None
    
    # Retry connection 3 times
    for attempt in range(1, 4):
        try:
            print(f"  - Attempt {attempt}...")
            # The library attempts to read the chip ID here
            # You should see [TX] -> AA 00 ... and [RX] <- BB ... if working
            sensor = adafruit_bno055.BNO055_UART(uart)
            print("  -> Success!")
            break
        except Exception as e:
            print(f"  -> Failed ({e})")
            if attempt < 3:
                print("     Retrying in 2 seconds...")
                time.sleep(2.0)
            else:
                print("\n[!] Could not connect to BNO055 after 3 attempts.")
                print("Troubleshooting Checklist:")
                print("1. Wiring on Pins 31 & 28:")
                print("   - Ensure TX goes to RX and RX goes to TX.")
                print("   - Note: Standard UART5 is Pin 32/33. Double check your specific hardware pinout.")
                print("2. PS1 Pin: MUST be connected to 3.3V (Vin).")
                print("3. Power Cycle: Unplug and replug the sensor power to reset mode.")
                sys.exit(1)

    print("\nSensor initialized! Reading data...")
    print("Press Ctrl+C to stop")
    print("-" * 65)
    print(f"{'Temp':<6} | {'Euler (Head, Roll, Pitch)':<30} | {'Calibration (Sys, Gyro, Accel, Mag)'}")
    print("-" * 65)

    # 3. Main Loop
    while True:
        try:
            # We temporarily silence debugging for the loop so the table isn't messy
            # (Optional: Comment this out if you WANT to see the raw data stream continuously)
            # uart.read = raw_uart.read 
            # uart.write = raw_uart.write
            
            # Read Euler Angles (Heading, Roll, Pitch)
            euler = sensor.euler
            
            # Read Temperature
            temp = sensor.temperature
            
            # Read Calibration Status
            calib = sensor.calibration_status

            # Format data for printing
            e_str = f"{euler[0]:.1f}, {euler[1]:.1f}, {euler[2]:.1f}" if euler and None not in euler else "Reading..."
            t_str = f"{temp}C" if temp is not None else "??"
            c_str = f"{calib[0]}, {calib[1]}, {calib[2]}, {calib[3]}" if calib else "?, ?, ?, ?"

            print(f"{t_str:<6} | {e_str:<30} | {c_str}")

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error reading data: {e}")
            time.sleep(0.5)

if __name__ == "__main__":
    main()
