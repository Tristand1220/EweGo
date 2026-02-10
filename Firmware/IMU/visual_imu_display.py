#!/usr/bin/env python3
"""
BNO055 IMU Visual Display Script for Raspberry Pi CM4
Serial Port: /dev/ttyAMA5 (UART)

Features:
- Terminal-based visual representation
- Real-time updating display
- XYZ Euler angles visualization
- Gravity vector display
- Linear acceleration display
- Heading compass indicator
"""

import serial
import signal
import sys
import time
import struct
import math
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional

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
        """Write a byte to a register using UART protocol"""
        cmd = bytes([UART_START_BYTE, 0x00, register, 1, value])
        self.serial.write(cmd)

        # Read response
        response = self.serial.read(2)
        if len(response) < 2:
            raise IOError("Write timeout - no response")

        if response[0] != UART_ERROR_BYTE or response[1] != 0x01:
            raise IOError(f"Write error: {response.hex()}")

    def _read_byte(self, register: int) -> int:
        """Read a single byte from a register"""
        data = self._read_bytes(register, 1)
        return data[0]

    def _read_bytes(self, register: int, length: int, max_retries: int = 3) -> bytes:
        """Read multiple bytes starting from a register with automatic retry"""
        last_error = None

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    self.serial.reset_input_buffer()
                    time.sleep(0.01)

                cmd = bytes([UART_START_BYTE, 0x01, register, length])
                self.serial.write(cmd)

                header = self.serial.read(2)
                if len(header) < 2:
                    raise IOError("Read timeout - no response header")

                if header[0] == UART_ERROR_BYTE:
                    error_code = header[1]
                    error_msg = self._get_error_description(error_code)

                    if error_code in [0x07]:
                        last_error = IOError(f"Read error 0x{error_code:02X} ({error_msg})")
                        continue
                    else:
                        raise IOError(f"Read error 0x{error_code:02X} ({error_msg})")

                if header[0] != UART_RESPONSE_BYTE:
                    raise IOError(f"Unexpected response byte: 0x{header[0]:02X}")

                resp_length = header[1]
                data = self.serial.read(resp_length)

                if len(data) < resp_length:
                    raise IOError(f"Read timeout - got {len(data)} bytes, expected {resp_length}")

                return data

            except IOError as e:
                last_error = e
                if attempt < max_retries - 1:
                    continue
                else:
                    raise

        if last_error:
            raise last_error
        else:
            raise IOError("Read failed after all retries")

    def _get_error_description(self, error_code: int) -> str:
        """Get human-readable description of BNO055 UART error codes"""
        error_codes = {
            0x01: "WRITE_SUCCESS",
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
# Terminal Display Utilities
# ============================================================================

class TerminalDisplay:
    """Terminal-based visual display utilities"""

    # ANSI escape codes
    CLEAR_SCREEN = '\033[2J'
    CURSOR_HOME = '\033[H'
    HIDE_CURSOR = '\033[?25l'
    SHOW_CURSOR = '\033[?25h'

    @staticmethod
    def clear():
        """Clear screen and move cursor to home"""
        print(TerminalDisplay.CLEAR_SCREEN + TerminalDisplay.CURSOR_HOME, end='')

    @staticmethod
    def hide_cursor():
        """Hide terminal cursor"""
        print(TerminalDisplay.HIDE_CURSOR, end='')

    @staticmethod
    def show_cursor():
        """Show terminal cursor"""
        print(TerminalDisplay.SHOW_CURSOR, end='')

    @staticmethod
    def create_bar(value: float, min_val: float, max_val: float, width: int = 40) -> str:
        """Create a horizontal bar graph

        Args:
            value: Current value
            min_val: Minimum value for scale
            max_val: Maximum value for scale
            width: Width of bar in characters
        """
        # Normalize value to 0-1 range
        normalized = (value - min_val) / (max_val - min_val)
        normalized = max(0.0, min(1.0, normalized))

        # Calculate bar length
        bar_length = int(normalized * width)

        # Create bar with center marker
        center = width // 2
        bar = ""
        for i in range(width):
            if i == center:
                bar += "|"
            elif i < bar_length:
                bar += "█"
            else:
                bar += "░"

        return bar

    @staticmethod
    def create_compass(heading: float, size: int = 15) -> list:
        """Create ASCII compass display

        Args:
            heading: Heading in degrees (0-360)
            size: Diameter of compass in characters (must be odd)

        Returns:
            List of strings representing compass lines with border
        """
        if size % 2 == 0:
            size += 1

        center = size // 2
        lines = []

        # ASCII aspect ratio compensation (characters are ~2:1 height:width)
        # We need to stretch horizontally to compensate
        aspect_ratio = 2.0

        # Convert heading to radians (0° = North = up)
        heading_rad = math.radians(heading)

        # Calculate needle direction
        needle_dx = math.sin(heading_rad)
        needle_dy = -math.cos(heading_rad)

        # Create compass grid
        for y in range(size):
            line = ""
            for x in range(size):
                # Calculate position relative to center with aspect correction
                dx = (x - center) / aspect_ratio
                dy = y - center
                dist = math.sqrt(dx*dx + dy*dy)

                # Determine what to draw
                char = " "

                # Check cardinal directions first (higher priority)
                if y == 0 and x == center:
                    char = "N"
                elif y == size - 1 and x == center:
                    char = "S"
                elif x == 0 and abs(y - center) <= 0.5:
                    char = "W"
                elif x == size - 1 and abs(y - center) <= 0.5:
                    char = "E"
                # Check if on circle perimeter
                elif abs(dist - (center - 1)) < 0.6:
                    char = "○"
                # Check if on needle
                elif dist < center - 1:
                    # Calculate if this point is close to the needle line
                    # Normalize the position vector
                    if dist > 0.5:
                        norm_dx = dx / dist
                        norm_dy = dy / dist
                        # Dot product to see if point is in needle direction
                        dot = norm_dx * needle_dx + norm_dy * needle_dy
                        if dot > 0.85:  # Close to needle direction
                            char = "▲"
                        elif dist < 0.8:
                            char = "●"
                    else:
                        char = "●"

                line += char

            lines.append(line)

        # Add borders around the compass
        bordered_lines = []
        border_width = size

        # Top border
        bordered_lines.append("┌" + "─" * border_width + "┐")

        # Content with side borders
        for line in lines:
            bordered_lines.append("│" + line + "│")

        # Bottom border
        bordered_lines.append("└" + "─" * border_width + "┘")

        return bordered_lines

    @staticmethod
    def create_angle_indicator(angle: float, label: str, range_deg: float = 180) -> str:
        """Create a horizontal angle indicator

        Args:
            angle: Angle in degrees
            label: Label for the indicator
            range_deg: Range of angles to display (+/-)
        """
        width = 40
        # Clamp angle to range
        angle = max(-range_deg, min(range_deg, angle))

        # Normalize to 0-1
        normalized = (angle + range_deg) / (2 * range_deg)
        pos = int(normalized * width)

        # Create indicator
        indicator = ""
        for i in range(width):
            if i == width // 2:
                if i == pos:
                    indicator += "┼"
                else:
                    indicator += "┊"
            elif i == pos:
                indicator += "▼"
            else:
                indicator += "─"

        return f"{label:6s} [{indicator}] {angle:+7.2f}°"

    @staticmethod
    def create_vector_bar(value: float, label: str, max_abs: float = 10.0) -> str:
        """Create a bidirectional bar for vector components

        Args:
            value: Vector component value
            label: Label (e.g., "X", "Y", "Z")
            max_abs: Maximum absolute value for scale
        """
        width = 30
        center = width // 2

        # Clamp and normalize
        value = max(-max_abs, min(max_abs, value))
        normalized = value / max_abs

        # Calculate bar position
        if normalized >= 0:
            bar_start = center
            bar_end = center + int(normalized * center)
        else:
            bar_end = center
            bar_start = center + int(normalized * center)

        # Create bar
        bar = ""
        for i in range(width):
            if i == center:
                bar += "|"
            elif bar_start <= i <= bar_end or bar_end <= i <= bar_start:
                bar += "█"
            else:
                bar += "░"

        return f"{label:2s} [{bar}] {value:+7.3f}"


# ============================================================================
# Visual Polling Application
# ============================================================================

class BNO055VisualPoller:
    """Visual polling application with terminal display"""

    def __init__(self, port: str = "/dev/ttyAMA5", poll_rate_hz: float = 10.0):
        self.sensor = BNO055(port=port)
        self.poll_interval = 1.0 / poll_rate_hz
        self.running = False
        self.display = TerminalDisplay()
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup handlers for graceful shutdown"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.running = False

    def _render_display(self, euler: EulerAngles, gravity: Vector3, accel: Vector3,
                       calib: CalibrationStatus, poll_count: int):
        """Render the visual display"""
        # Move cursor to home (don't clear to reduce flicker)
        print(self.display.CURSOR_HOME, end='')

        lines = []
        lines.append("=" * 70)
        lines.append("BNO055 IMU Visual Display".center(70))
        lines.append("=" * 70)
        lines.append("")

        # Heading compass
        lines.append("HEADING COMPASS:")
        compass_lines = self.display.create_compass(euler.heading, size=15)
        for line in compass_lines:
            lines.append("  " + line)
        lines.append(f"  Heading: {euler.heading:7.2f}°".center(70))
        lines.append("")

        # Euler angles
        lines.append("EULER ANGLES:")
        lines.append("  " + self.display.create_angle_indicator(euler.roll, "Roll", 180))
        lines.append("  " + self.display.create_angle_indicator(euler.pitch, "Pitch", 180))
        lines.append("")

        # Gravity vector
        lines.append("GRAVITY VECTOR (m/s²):")
        lines.append("  " + self.display.create_vector_bar(gravity.x, "X", 12.0))
        lines.append("  " + self.display.create_vector_bar(gravity.y, "Y", 12.0))
        lines.append("  " + self.display.create_vector_bar(gravity.z, "Z", 12.0))
        lines.append(f"  Magnitude: {math.sqrt(gravity.x**2 + gravity.y**2 + gravity.z**2):.3f} m/s²")
        lines.append("")

        # Linear acceleration
        lines.append("LINEAR ACCELERATION (m/s²):")
        lines.append("  " + self.display.create_vector_bar(accel.x, "X", 5.0))
        lines.append("  " + self.display.create_vector_bar(accel.y, "Y", 5.0))
        lines.append("  " + self.display.create_vector_bar(accel.z, "Z", 5.0))
        lines.append(f"  Magnitude: {math.sqrt(accel.x**2 + accel.y**2 + accel.z**2):.3f} m/s²")
        lines.append("")

        # Calibration status
        cal_symbols = ["○", "◔", "◑", "●"]
        lines.append("CALIBRATION STATUS:")
        lines.append(f"  System: {cal_symbols[calib.system]} [{calib.system}/3]  "
                    f"Gyro: {cal_symbols[calib.gyro]} [{calib.gyro}/3]  "
                    f"Accel: {cal_symbols[calib.accel]} [{calib.accel}/3]  "
                    f"Mag: {cal_symbols[calib.mag]} [{calib.mag}/3]")
        lines.append("")

        # Status
        lines.append("=" * 70)
        lines.append(f"Poll count: {poll_count}  |  Rate: {1.0/self.poll_interval:.1f} Hz  |  Press Ctrl+C to exit")
        lines.append("=" * 70)

        # Print all lines (pad to consistent height to prevent scrolling)
        display_height = 40
        while len(lines) < display_height:
            lines.append(" " * 70)

        for line in lines[:display_height]:
            # Ensure each line is exactly 70 chars wide
            print(f"{line:<70}")

    def run(self):
        """Main polling loop"""
        # Initialize sensor
        if not self.sensor.open():
            print("Failed to initialize sensor. Exiting.")
            return 1

        try:
            # Setup display
            self.display.clear()
            self.display.hide_cursor()

            self.running = True
            poll_count = 0

            while self.running:
                loop_start = time.monotonic()

                try:
                    # Read sensor data
                    euler = self.sensor.get_euler()
                    time.sleep(0.005)
                    gravity = self.sensor.get_gravity()
                    time.sleep(0.005)
                    accel = self.sensor.get_linear_acceleration()
                    time.sleep(0.005)
                    calib = self.sensor.get_calibration()

                    poll_count += 1

                    # Render display
                    self._render_display(euler, gravity, accel, calib, poll_count)

                except IOError as e:
                    # Display error without disrupting the layout
                    print(f"\n[WARNING] Sensor read error: {e}")
                    time.sleep(0.1)
                    continue

                # Maintain consistent poll rate
                elapsed = time.monotonic() - loop_start
                sleep_time = self.poll_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            # Graceful shutdown
            self.display.show_cursor()
            self.display.clear()
            print("=" * 70)
            print("Shutting down...")
            self.sensor.close()
            print("Sensor closed. Goodbye!")
            print("=" * 70)

        return 0


# ============================================================================
# Entry Point
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="BNO055 IMU Visual Display Script",
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

    poller = BNO055VisualPoller(port=args.port, poll_rate_hz=args.rate)
    sys.exit(poller.run())


if __name__ == "__main__":
    main()
