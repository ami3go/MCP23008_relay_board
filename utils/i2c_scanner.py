"""
i2c_scanner.py
==============
I2C bus scanner for use with an FTDI FT232H USB-to-I2C adapter
and Adafruit Blinka.

Scans the full 7-bit address space (0x00–0x7F), skips reserved
addresses, and prints a nicely formatted report.  Also highlights
any addresses that match known MCP23008 relay-board addresses.

Dependencies
------------
    pip install adafruit-blinka

Usage
-----
    # From the command line:
    python i2c_scanner.py

    # Or import and call from your own code:
    from i2c_scanner import scan_i2c
    devices = scan_i2c()
"""

from __future__ import annotations

import os
import sys

# Must be set before importing board/busio
if "BLINKA_FT232H" not in os.environ:
    os.environ["BLINKA_FT232H"] = "1"

import board   # type: ignore  (blinka)
import busio   # type: ignore  (blinka)


# ---------------------------------------------------------------------------
# Known device registry  (address: description)
# ---------------------------------------------------------------------------
KNOWN_DEVICES: dict[int, str] = {
    # MCP23008 / MCP23017 range
    0x20: "MCP23008/MCP23017 – Relay Board 0 (A2=0 A1=0 A0=0)",
    0x21: "MCP23008/MCP23017 – Relay Board 1 (A2=0 A1=0 A0=1)",
    0x22: "MCP23008/MCP23017 – Relay Board 2 (A2=0 A1=1 A0=0)",
    0x23: "MCP23008/MCP23017 – Relay Board 3 (A2=0 A1=1 A0=1)",
    0x24: "MCP23008/MCP23017 – Relay Board 4 (A2=1 A1=0 A0=0)",
    0x25: "MCP23008/MCP23017 (A2=1 A1=0 A0=1)",
    0x26: "MCP23008/MCP23017 (A2=1 A1=1 A0=0)",
    0x27: "MCP23008/MCP23017 (A2=1 A1=1 A0=1)",
    # Common sensors / modules
    0x3C: "SSD1306 OLED display (128x64 / 128x32)",
    0x3D: "SSD1306 OLED display (alt address)",
    0x48: "ADS1015/ADS1115 ADC  (ADDR=GND)",
    0x49: "ADS1015/ADS1115 ADC  (ADDR=VCC)",
    0x4A: "ADS1015/ADS1115 ADC  (ADDR=SDA)",
    0x4B: "ADS1015/ADS1115 ADC  (ADDR=SCL)",
    0x50: "24Cxx EEPROM / AT24C32",
    0x57: "DS3231 RTC EEPROM",
    0x68: "DS3231 / MPU-6050 / PCF8523 RTC",
    0x69: "MPU-6050 (AD0=1) / ITG-3200",
    0x76: "BMP280 / BME280 pressure sensor (SDO=GND)",
    0x77: "BMP280 / BME280 pressure sensor (SDO=VCC)",
}

# 7-bit addresses reserved by the I2C spec (skip during scan)
# 0x00       – general call
# 0x01–0x07  – reserved
# 0x78–0x7F  – reserved (10-bit address prefix)
RESERVED: set[int] = set(range(0x00, 0x08)) | set(range(0x78, 0x80))


# ---------------------------------------------------------------------------
# Core scan function
# ---------------------------------------------------------------------------

def scan_i2c(
    scl_pin=None,
    sda_pin=None,
    frequency: int = 100_000,
) -> list[int]:
    """
    Scan the I2C bus and return a list of responding 7-bit addresses.

    Parameters
    ----------
    scl_pin :
        SCL pin object (defaults to board.SCL for FT232H).
    sda_pin :
        SDA pin object (defaults to board.SDA for FT232H).
    frequency :
        I2C clock in Hz (default 100 000).

    Returns
    -------
    list[int]
        Sorted list of addresses that acknowledged.
    """
    scl = scl_pin or board.SCL
    sda = sda_pin or board.SDA

    print("=" * 60)
    print("  I2C Scanner  –  FT232H / Adafruit Blinka")
    print("=" * 60)
    print(f"  SCL pin : {scl}")
    print(f"  SDA pin : {sda}")
    print(f"  Frequency: {frequency:,} Hz")
    print("-" * 60)

    try:
        i2c = busio.I2C(scl, sda, frequency=frequency)
    except Exception as exc:
        print(f"\n[ERROR] Could not open I2C bus: {exc}")
        print("  • Is the FT232H plugged in?")
        print("  • Is BLINKA_FT232H=1 set in the environment?")
        print("  • Do you have the required udev rules / drivers?")
        sys.exit(1)

    # Wait until the bus is ready
    while not i2c.try_lock():
        pass

    found: list[int] = []
    try:
        print("\n  Scanning addresses 0x08 – 0x77 …\n")
        for addr in range(0x00, 0x80):
            if addr in RESERVED:
                continue
            try:
                i2c.writeto(addr, b"")
                found.append(addr)
            except OSError:
                pass  # No ACK – no device at this address
    finally:
        i2c.unlock()
        i2c.deinit()

    return sorted(found)


# ---------------------------------------------------------------------------
# Pretty-print results
# ---------------------------------------------------------------------------

def _print_results(found: list[int]) -> None:
    if not found:
        print("  No I2C devices found.\n")
        print("  Troubleshooting:")
        print("   • Check wiring (SDA/SCL swapped?)")
        print("   • Verify pull-up resistors (4.7 kΩ to 3.3 V or 5 V)")
        print("   • Confirm power is supplied to the boards")
        print("=" * 60)
        return

    print(f"  Found {len(found)} device(s):\n")
    print(f"  {'Address (hex)':<16} {'Address (dec)':<16} {'Device'}")
    print("  " + "-" * 56)

    for addr in found:
        desc = KNOWN_DEVICES.get(addr, "Unknown device")
        print(f"  0x{addr:02X}           {addr:<16} {desc}")

    print()

    # Summary for relay boards specifically
    relay_addrs = [a for a in found if 0x20 <= a <= 0x24]
    if relay_addrs:
        print("  ── Relay Board Summary ──────────────────────────────")
        for addr in relay_addrs:
            idx = addr - 0x20
            print(f"  Board {idx}  →  0x{addr:02X}  ✓ responding")
        missing = [i for i in range(5) if (0x20 + i) not in relay_addrs]
        if missing:
            print()
            for idx in missing:
                print(f"  Board {idx}  →  0x{0x20 + idx:02X}  ✗ NOT found")
        print()

    print("=" * 60)


# ---------------------------------------------------------------------------
# 16-column hex grid (like classic i2cdetect output)
# ---------------------------------------------------------------------------

def _print_hex_grid(found: list[int]) -> None:
    found_set = set(found)
    print("\n  i2cdetect-style map (-- = not scanned, XX = found)\n")
    print("       " + "  ".join(f"{col:X}" for col in range(16)))
    print("  " + "-" * 54)
    for row in range(8):
        base = row * 16
        row_label = f"  {base:02X}: "
        cells = []
        for col in range(16):
            addr = base + col
            if addr in RESERVED:
                cells.append("--")
            elif addr in found_set:
                cells.append(f"{addr:02X}")
            else:
                cells.append("  ")
        print(row_label + " ".join(cells))
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    devices = scan_i2c()
    _print_results(devices)
    _print_hex_grid(devices)