from __future__ import annotations

import os
from typing import Dict, Optional

# Ensure Blinka FT232H backend
if "BLINKA_FT232H" not in os.environ:
    os.environ["BLINKA_FT232H"] = "1"

import board
import busio
from digitalio import Direction
from adafruit_mcp230xx.mcp23008 import MCP23008

"""
relay_board.py
==============
Python library for controlling up to 5 x 8-channel relay boards
connected via I2C using MCP23008 GPIO expander ICs and an FTDI FT232H
USB-to-I2C interface on the PC side.

Hardware assumptions
--------------------
* Each relay board uses one MCP23008 (8-bit I/O expander).
* MCP23008 I2C base address: 0x20.
* Board address is set by the A0/A1/A2 hardware pins on each IC:
    Board 0 → A2=0 A1=0 A0=0 → 0x20
    Board 1 → A2=0 A1=0 A0=1 → 0x21
    Board 2 → A2=0 A1=1 A0=0 → 0x22
    Board 3 → A2=0 A1=1 A0=1 → 0x23
    Board 4 → A2=1 A1=0 A0=0 → 0x24
* GP0–GP7 drive relay channels 1–8 respectively.
* Relay is ACTIVE HIGH by default (set active_low=True if your board
  energises the coil when the output is pulled LOW).
"""

class ResistorSelector:
    """
    High-level controller for MCP23008-based relay boards that select resistors
    by closest nominal resistance value.

    Safely manages USB/I2C lifetime (no libusb double-open issues).
    """

    MCP_BASE = 0x20
    MAX_BOARDS = 5
    CHANNELS = 8
'''
Functional version of the resistor selector.

All state is explicit – no classes, no hidden instance variables.
The "controller handle" is just a plain dict returned by open_controller()
and passed into every function that needs hardware access.

Resistor map (same hardware as resistor_selector.py):
    Channel 1 →   1.3 kΩ
    Channel 2 →   1.6 kΩ
    Channel 3 →  13   kΩ
    Channel 4 →  30   kΩ
    Channel 5 →  68   kΩ
    Channel 6 → 180   kΩ
    Channel 7 →  (unused)
    Channel 8 →  (unused)
'''
    RESISTOR_MAP = {
        6: 1_300,
        5: 1_600,
        4: 3_000,
        3: 34_000,
        2: 68_000,
        1: 180_000,
    }

    RESISTOR_LABELS = {
        6: "1.3 kΩ",
        5: "1.6 kΩ",
        4: "3 kΩ",
        3: "34 kΩ",
        2: "68 kΩ",
        1: "180 kΩ",
    }

    # ─────────────────────────────────────────────

    def __init__(
        self,
        num_boards: int = 5,
        active_low: bool = False,
        dry_run: bool = False,
    ):
        if not 1 <= num_boards <= self.MAX_BOARDS:
            raise ValueError("num_boards must be 1–5")

        self.num_boards = num_boards
        self.active_low = active_low
        self.dry_run = dry_run

        self._i2c = None
        self._boards: Dict[int, MCP23008] = {}
        self._selections: Dict[int, Optional[dict]] = {}

    # ─────────────────────────────────────────────
    # Context manager
    # ─────────────────────────────────────────────

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ─────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────

    def open(self) -> None:
        if self._i2c is not None:
            return

        self._i2c = busio.I2C(board.SCL, board.SDA)

        for i in range(self.num_boards):
            addr = self.MCP_BASE + i
            mcp = MCP23008(self._i2c, address=addr)

            for p in range(self.CHANNELS):
                pin = mcp.get_pin(p)
                pin.direction = Direction.OUTPUT
                pin.value = self.active_low

            self._boards[i] = mcp
            self._selections[i] = None

    def close(self) -> None:
        if not self.dry_run:
            for b in range(self.num_boards):
                self._all_off(b)

        self._boards.clear()
        self._selections.clear()

        if self._i2c:
            try:
                self._i2c.deinit()
            except Exception:
                pass
            self._i2c = None

    # ─────────────────────────────────────────────
    # Core logic
    # ─────────────────────────────────────────────

    @staticmethod
    def _parse_resistance(text: str) -> float:
        t = text.strip().lower().replace("Ω", "")
        mult = 1

        if t.endswith("k"):
            mult = 1_000
            t = t[:-1]
        elif t.endswith("m"):
            mult = 1_000_000
            t = t[:-1]

        return float(t) * mult

    def _closest(self, ohms: float) -> dict:
        best = None

        for ch, nom in self.RESISTOR_MAP.items():
            err = abs(nom - ohms) / ohms
            if best is None or err < best["err"]:
                best = {
                    "channel": ch,
                    "nominal": nom,
                    "label": self.RESISTOR_LABELS[ch],
                    "err": err,
                }

        return best

    def _all_off(self, board: int) -> None:
        if self.dry_run:
            return

        mcp = self._boards[board]
        for p in range(self.CHANNELS):
            mcp.get_pin(p).value = self.active_low

    # ─────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────

    def select(self, board: int, resistance: str) -> dict:
        if board not in self._boards:
            raise ValueError("Invalid board index or controller not open")

        ohms = self._parse_resistance(resistance)
        best = self._closest(ohms)

        self._all_off(board)

        if not self.dry_run:
            pin = self._boards[board].get_pin(best["channel"] - 1)
            pin.value = not self.active_low

        result = {
            "board": board,
            "channel": best["channel"],
            "label": best["label"],
            "requested_ohms": ohms,
            "nominal_ohms": best["nominal"],
            "error_pct": best["err"] * 100,
        }

        self._selections[board] = result
        return result

    def off(self, board: int) -> None:
        self._all_off(board)
        self._selections[board] = None

    def off_all(self) -> None:
        for b in range(self.num_boards):
            self.off(b)

    def status(self) -> Dict[int, Optional[dict]]:
        return dict(self._selections)