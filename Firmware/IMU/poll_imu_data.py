#!/usr/bin/env python3
"""
BNO055 IMU Polling Script for Raspberry Pi CM4
Serial Port: /dev/ttyAMA5 (UART)

Features:
- Graceful shutdown on SIGINT/SIGTERM
- Rerunnable without issues
- Configurable polling rate
- Comprehensive error handling
"""

import serial
import signal
import sys
import time
import struct
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional, Tuple

# ============================================================================
# BNO055 Constants
# ============================================================================

class Register(IntEnum):
    """BNO055 Register Addresses"""
    # Page 0 registers
    CHIP_ID = 0x00
    PAGE_ID = 0x07
    OPR_MODE = 0x3D
    PWR_MODE = 0x3E
    SYS_TRIGGER = 0x3F
    
    # Calibration status
    CALIB_STAT = 0x35
    
    # Euler angles
    EUL_HEADING_LSB = 0x1A
    EUL_ROLL_LSB = 0x1C
    EUL_PITCH_LSB = 0x1E
    
    # Quaternion
    QUA_W_LSB = 0x20
    QUA_X_LSB = 0x22
    QUA_Y_LSB = 0x24
    QUA_Z_LSB = 0x26
    
    # Linear acceleration
    LIA_X_LSB = 0x28
    LIA_Y_LSB = 0x2A
    LIA_Z_LSB = 0x2C
    
    # Gravity vector
    GRV_X_LSB = 0x2E
    GRV_Y_LSB = 0x30
    GRV_Z_LSB = 0x32
    
    # Accelerometer
    ACC_X_LSB = 0x08
    ACC_Y_LSB = 0x0A
    ACC_Z_LSB = 0x0C
    
    # Gyroscope
    GYR_X_LSB = 0x14
    GYR_Y_LSB = 0x16
    GYR_Z_LSB = 0x18
    
    # Magnetometer
    MAG_X_LSB = 0x0E
    MAG_Y_LSB = 0x10
    MAG_Z_LSB = 0x12
    
    # Temperature
    TEMP = 0x34


class OperationMode(IntEnum):
    """BNO055 Operation Modes"""
    CONFIG = 0x00
    ACCONLY = 0x01
    MAGONLY = 0x02
    GYROONLY = 0x03
    ACCMAG = 0x04
    ACCGYRO = 0x05
    MAGGYRO = 0x06
    AMG = 0x07
    IMU = 0x08
    COMPASS = 0x09
    M4G = 0x0A
    NDOF_FMC_OFF = 0x0B
    NDOF = 0x0C  # Full fusion mode


class PowerMode(IntEnum):
    """BNO055 Power Modes"""
    NORMAL = 0x00
    LOW = 0x01
    SUSPEND = 0x02


# UART Protocol constants
UART_START_BYTE = 0xAA
UART_RESPONSE_BYTE = 0xBB
UART_ERROR_BYTE = 0xEE

BNO055_CHIP_ID = 0xA0

# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class EulerAngles:
    heading: float  # degrees
    roll: float     # degrees
    pitch: float    # degrees


@dataclass
class Quaternion:
    w: float
    x: float
    y: float
    z: float


@dataclass
class Vector3:
    x: float
    y: float
    z: float


@dataclass
class CalibrationStatus:
    system: int  # 0-3
    gyro: int    # 0-3
    accel: int   # 0-3
    mag: int     # 0-3
    
    @property
    def fully_calibrated(self) -> bool:
        return all(v == 3 for v in [self.system, self.gyro, self.accel, self.mag])


# ============================================================================
# BNO055 UART Driver
# ============================================================================

class BNO055:
    """BNO055 IMU driver using UART protocol"""
    
    def __init__(self, port: str = "/dev/ttyAMA5", baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial: Optional[serial.Serial] = None
        self._initialized = False
    
    def open(self) -> bool:
        """Open serial connection and initialize the sensor"""
        try:
            # Close any existing connection
            self.close()

            # Open serial port
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout
            )

            # Clear any stale data
            self.serial.reset_input_buffer()
            self.serial.reset_output_buffer()

            # Give sensor time to stabilize after port open
            time.sleep(1.0)

            # Try to read chip ID with multiple retries (no software reset)
            chip_id = None
            max_attempts = 5

            for attempt in range(max_attempts):
                try:
                    print(f"Reading chip ID (attempt {attempt + 1}/{max_attempts})...")

                    # Clear buffers before each attempt
                    self.serial.reset_input_buffer()

                    chip_id = self._read_byte(Register.CHIP_ID)
                    print(f"Got chip ID: 0x{chip_id:02X}")

                    if chip_id == BNO055_CHIP_ID:
                        print("✓ Chip ID verified!")
                        break
                    else:
                        print(f"Wrong chip ID: 0x{chip_id:02X} (expected 0x{BNO055_CHIP_ID:02X})")

                except Exception as e:
                    print(f"Chip ID read failed: {e}")

                # Wait between retries (except on last attempt)
                if attempt < max_attempts - 1:
                    time.sleep(0.5)

            if chip_id != BNO055_CHIP_ID:
                print(f"Error: Invalid chip ID: 0x{chip_id:02X} (expected 0x{BNO055_CHIP_ID:02X})")
                return False

            # Give sensor additional time to fully boot after chip ID verification
            print("Waiting for sensor to fully initialize...")
            time.sleep(0.65)  # BNO055 needs ~650ms post-reset initialization time

            # Reset to config mode first
            print("Configuring sensor...")
            self._set_mode(OperationMode.CONFIG)
            time.sleep(0.05)

            # Set to normal power mode
            self._write_byte(Register.PWR_MODE, PowerMode.NORMAL)
            time.sleep(0.01)

            # Select page 0
            self._write_byte(Register.PAGE_ID, 0x00)
            time.sleep(0.01)

            # Set operation mode to NDOF (full fusion)
            print("Setting NDOF fusion mode...")
            self._set_mode(OperationMode.NDOF)
            time.sleep(0.02)

            self._initialized = True
            print(f"BNO055 initialized successfully on {self.port}")
            return True

        except serial.SerialException as e:
            print(f"Serial error: {e}")
            return False
        except Exception as e:
            print(f"Initialization error: {e}")
            return False
    
    def close(self):
        """Close serial connection and put sensor in config mode"""
        if self.serial and self.serial.is_open:
            try:
                # Put sensor back in config mode for clean state
                if self._initialized:
                    self._set_mode(OperationMode.CONFIG)
                    time.sleep(0.02)
            except:
                pass
            finally:
                self.serial.close()
                self.serial = None
        self._initialized = False
    
    def _write_byte(self, register: int, value: int):
        """Write a byte to a register using UART protocol

        Note: BNO055 UART write success is indicated by 0xEE 0x01 response
        """
        # UART write command: [0xAA, 0x00, register, length, data...]
        cmd = bytes([UART_START_BYTE, 0x00, register, 1, value])
        self.serial.write(cmd)

        # Read response
        response = self.serial.read(2)
        if len(response) < 2:
            raise IOError("Write timeout - no response")

        # BNO055 UART protocol: successful write returns 0xEE 0x01
        # Any other response indicates an error
        if response[0] != UART_ERROR_BYTE or response[1] != 0x01:
            raise IOError(f"Write error: {response.hex()}")
    
    def _read_byte(self, register: int) -> int:
        """Read a single byte from a register"""
        data = self._read_bytes(register, 1)
        return data[0]
    
    def _read_bytes(self, register: int, length: int, max_retries: int = 3) -> bytes:
        """Read multiple bytes starting from a register with automatic retry on transient errors

        Args:
            register: Register address to read from
            length: Number of bytes to read
            max_retries: Maximum number of retry attempts for transient errors

        Returns:
            Bytes read from the sensor

        Raises:
            IOError: If read fails after all retries
        """
        last_error = None

        for attempt in range(max_retries):
            try:
                # Clear input buffer to prevent sync issues from previous transactions
                if attempt > 0:
                    self.serial.reset_input_buffer()
                    time.sleep(0.01)  # Small delay before retry

                # UART read command: [0xAA, 0x01, register, length]
                cmd = bytes([UART_START_BYTE, 0x01, register, length])
                self.serial.write(cmd)

                # Read response header
                header = self.serial.read(2)
                if len(header) < 2:
                    raise IOError("Read timeout - no response header")

                # Check for error response
                if header[0] == UART_ERROR_BYTE:
                    error_code = header[1]
                    error_msg = self._get_error_description(error_code)

                    # For transient errors, retry; for persistent errors, fail immediately
                    if error_code in [0x07]:  # MAX_LENGTH_ERROR - often transient
                        last_error = IOError(f"Read error 0x{error_code:02X} ({error_msg})")
                        continue  # Retry
                    else:
                        # Non-transient error, fail immediately
                        raise IOError(f"Read error 0x{error_code:02X} ({error_msg})")

                if header[0] != UART_RESPONSE_BYTE:
                    raise IOError(f"Unexpected response byte: 0x{header[0]:02X}")

                # header[1] contains the length
                resp_length = header[1]
                data = self.serial.read(resp_length)

                if len(data) < resp_length:
                    raise IOError(f"Read timeout - got {len(data)} bytes, expected {resp_length}")

                return data

            except IOError as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue  # Retry
                else:
                    raise  # Re-raise on last attempt

        # If we get here, all retries failed
        if last_error:
            raise last_error
        else:
            raise IOError("Read failed after all retries")

    def _get_error_description(self, error_code: int) -> str:
        """Get human-readable description of BNO055 UART error codes"""
        error_codes = {
            0x01: "WRITE_SUCCESS",  # Actually success for writes
            0x02: "READ_FAIL",
            0x03: "WRITE_FAIL",
            0x04: "REGMAP_INVALID_ADDRESS",
            0x05: "REGMAP_WRITE_DISABLED",
            0x06: "WRONG_START_BYTE",
            0x07: "BUS_OVER_RUN_ERROR/MAX_LENGTH_ERROR",
            0x08: "MIN_LENGTH_ERROR",
            0x09: "RECEIVE_CHARACTER_TIMEOUT"
        }
        return error_codes.get(error_code, f"UNKNOWN_ERROR")
    
    def _set_mode(self, mode: OperationMode):
        """Set the operation mode"""
        self._write_byte(Register.OPR_MODE, mode)
    
    def _read_signed_16(self, register: int) -> int:
        """Read a signed 16-bit value from consecutive registers"""
        data = self._read_bytes(register, 2)
        return struct.unpack('<h', data)[0]
    
    def get_calibration(self) -> CalibrationStatus:
        """Get calibration status for all sensors"""
        status = self._read_byte(Register.CALIB_STAT)
        return CalibrationStatus(
            system=(status >> 6) & 0x03,
            gyro=(status >> 4) & 0x03,
            accel=(status >> 2) & 0x03,
            mag=status & 0x03
        )
    
    def get_euler(self) -> EulerAngles:
        """Get Euler angles (heading, roll, pitch) in degrees"""
        data = self._read_bytes(Register.EUL_HEADING_LSB, 6)
        heading, roll, pitch = struct.unpack('<hhh', data)
        # Scale: 1 degree = 16 LSB
        return EulerAngles(
            heading=heading / 16.0,
            roll=roll / 16.0,
            pitch=pitch / 16.0
        )
    
    def get_quaternion(self) -> Quaternion:
        """Get orientation as quaternion"""
        data = self._read_bytes(Register.QUA_W_LSB, 8)
        w, x, y, z = struct.unpack('<hhhh', data)
        # Scale: 1 quaternion unit = 2^14 LSB
        scale = 1.0 / (1 << 14)
        return Quaternion(
            w=w * scale,
            x=x * scale,
            y=y * scale,
            z=z * scale
        )
    
    def get_linear_acceleration(self) -> Vector3:
        """Get linear acceleration (without gravity) in m/s^2"""
        data = self._read_bytes(Register.LIA_X_LSB, 6)
        x, y, z = struct.unpack('<hhh', data)
        # Scale: 1 m/s^2 = 100 LSB
        return Vector3(x=x / 100.0, y=y / 100.0, z=z / 100.0)
    
    def get_gravity(self) -> Vector3:
        """Get gravity vector in m/s^2"""
        data = self._read_bytes(Register.GRV_X_LSB, 6)
        x, y, z = struct.unpack('<hhh', data)
        return Vector3(x=x / 100.0, y=y / 100.0, z=z / 100.0)
    
    def get_accelerometer(self) -> Vector3:
        """Get raw accelerometer data in m/s^2"""
        data = self._read_bytes(Register.ACC_X_LSB, 6)
        x, y, z = struct.unpack('<hhh', data)
        return Vector3(x=x / 100.0, y=y / 100.0, z=z / 100.0)
    
    def get_gyroscope(self) -> Vector3:
        """Get gyroscope data in degrees/second"""
        data = self._read_bytes(Register.GYR_X_LSB, 6)
        x, y, z = struct.unpack('<hhh', data)
        # Scale: 1 dps = 16 LSB
        return Vector3(x=x / 16.0, y=y / 16.0, z=z / 16.0)
    
    def get_magnetometer(self) -> Vector3:
        """Get magnetometer data in microtesla"""
        data = self._read_bytes(Register.MAG_X_LSB, 6)
        x, y, z = struct.unpack('<hhh', data)
        # Scale: 1 uT = 16 LSB
        return Vector3(x=x / 16.0, y=y / 16.0, z=z / 16.0)
    
    def get_temperature(self) -> int:
        """Get temperature in degrees Celsius"""
        return self._read_byte(Register.TEMP)


# ============================================================================
# Main Polling Application
# ============================================================================

class BNO055Poller:
    """Polling application with graceful shutdown"""
    
    def __init__(self, port: str = "/dev/ttyAMA5", poll_rate_hz: float = 10.0):
        self.sensor = BNO055(port=port)
        self.poll_interval = 1.0 / poll_rate_hz
        self.running = False
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Setup handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        sig_name = signal.Signals(signum).name
        print(f"\n[{sig_name}] Shutdown requested...")
        self.running = False
    
    def run(self):
        """Main polling loop"""
        print("=" * 60)
        print("BNO055 IMU Polling Script")
        print("=" * 60)
        print(f"Port: {self.sensor.port}")
        print(f"Poll rate: {1.0/self.poll_interval:.1f} Hz")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        
        # Initialize sensor
        if not self.sensor.open():
            print("Failed to initialize sensor. Exiting.")
            return 1
        
        self.running = True
        poll_count = 0
        
        try:
            while self.running:
                loop_start = time.monotonic()
                
                try:
                    # Read all sensor data with small delays to prevent overwhelming the sensor
                    euler = self.sensor.get_euler()
                    time.sleep(0.005)  # 5ms delay between reads
                    linear_accel = self.sensor.get_linear_acceleration()
                    time.sleep(0.005)
                    calib = self.sensor.get_calibration()
                    
                    # Clear line and print data
                    poll_count += 1
                    print(f"\r[{poll_count:6d}] "
                          f"Heading: {euler.heading:7.2f}° "
                          f"Roll: {euler.roll:7.2f}° "
                          f"Pitch: {euler.pitch:7.2f}° "
                          f"| Accel: ({linear_accel.x:6.2f}, {linear_accel.y:6.2f}, {linear_accel.z:6.2f}) m/s² "
                          f"| Cal: S{calib.system} G{calib.gyro} A{calib.accel} M{calib.mag}",
                          end="", flush=True)
                    
                except IOError as e:
                    # Only print error if retries were exhausted
                    print(f"\n[WARNING] Sensor read error (retries exhausted): {e}")
                    time.sleep(0.1)
                    continue
                
                # Maintain consistent poll rate
                elapsed = time.monotonic() - loop_start
                sleep_time = self.poll_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
        
        finally:
            # Graceful shutdown
            print("\n" + "=" * 60)
            print("Shutting down...")
            self.sensor.close()
            print("Sensor closed. Goodbye!")
            print("=" * 60)
        
        return 0


# ============================================================================
# Entry Point
# ============================================================================

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="BNO055 IMU Polling Script",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "-p", "--port",
        default="/dev/ttyAMA5",
        help="Serial port for BNO055"
    )
    parser.add_argument(
        "-r", "--rate",
        type=float,
        default=10.0,
        help="Polling rate in Hz"
    )
    
    args = parser.parse_args()
    
    poller = BNO055Poller(port=args.port, poll_rate_hz=args.rate)
    sys.exit(poller.run())


if __name__ == "__main__":
    main()
