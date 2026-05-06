from __future__ import annotations

import logging
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

Resistor map:
    Channel 1 → 180   kΩ  (GP0)
    Channel 2 →  68   kΩ  (GP1)
    Channel 3 →  34   kΩ  (GP2)
    Channel 4 →   3   kΩ  (GP3)
    Channel 5 →   1.6 kΩ  (GP4)
    Channel 6 →   1.3 kΩ  (GP5)
    Channel 7 →  (unused)
    Channel 8 →  (unused)
"""

log = logging.getLogger(__name__)


class ResistorSelector:
    """
    High-level controller for MCP23008-based relay boards that select resistors
    by closest nominal resistance value.

    Safely manages USB/I2C lifetime (no libusb double-open issues).

    Usage::

        with ResistorSelector(num_boards=1) as rs:
            rs.select(board_idx=0, resistance="34k")
    """

    MCP_BASE   = 0x20
    MAX_BOARDS = 5
    CHANNELS   = 8

    # FIX: was inconsistent with RESISTOR_LABELS and the old ''' comment block.
    # Channel numbers now match GP pin = channel - 1, consistent with select().
    RESISTOR_MAP = {
        1: 180_000,   # GP0
        2:  68_000,   # GP1
        3:  34_000,   # GP2
        4:   3_000,   # GP3
        5:   1_600,   # GP4
        6:   1_300,   # GP5
    }

    RESISTOR_LABELS = {
        1: "180 kΩ",
        2:  "68 kΩ",
        3:  "34 kΩ",
        4:   "3 kΩ",
        5: "1.6 kΩ",
        6: "1.3 kΩ",
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

        self._i2c: Optional[busio.I2C] = None
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
        """
        Shut down all relays and release the I2C/USB bus.

        FIX: _all_off() calls are now individually wrapped so that a USB
        device error (e.g. UsbError from a dropped FT232H) on one board
        cannot prevent the remaining boards from being turned off or block
        the mandatory _i2c.deinit() call, which was the root cause of:

            UsbError: [Errno None] b'libusb0-dll:err [_usb_reap_async]
            reaping request failed, win error: A device attached to the
            system is not functioning.'
        """
        if not self.dry_run:
            for b in range(self.num_boards):
                try:
                    self._all_off(b)
                except Exception as exc:
                    # Log but continue — we must reach deinit() regardless.
                    log.warning("close(): could not turn off board %d: %s", b, exc)

        self._boards.clear()
        self._selections.clear()

        if self._i2c is not None:
            try:
                self._i2c.deinit()
            except Exception as exc:
                log.warning("close(): I2C deinit failed: %s", exc)
            finally:
                # Always clear the reference so open() can reopen cleanly.
                self._i2c = None

    # ─────────────────────────────────────────────
    # Core logic
    # ─────────────────────────────────────────────

    @staticmethod
    def _parse_resistance(text: str) -> float:
        t = text.strip().lower().replace("Ω", "").replace("ω", "")
        mult = 1

        if t.endswith("k"):
            mult = 1_000
            t = t[:-1]
        elif t.endswith("m"):
            mult = 1_000_000
            t = t[:-1]

        return float(t) * mult

    def _closest(self, ohms: float) -> dict:
        best: Optional[dict] = None

        for ch, nom in self.RESISTOR_MAP.items():
            err = abs(nom - ohms) / ohms
            if best is None or err < best["err"]:
                best = {
                    "channel": ch,
                    "nominal": nom,
                    "label": self.RESISTOR_LABELS[ch],
                    "err": err,
                }

        return best  # type: ignore[return-value]  # always set (map is non-empty)

    def _all_off(self, board_idx: int) -> None:
        # FIX: renamed parameter from `board` to `board_idx` to avoid
        # shadowing the imported `board` module (adafruit_blinka).
        if self.dry_run:
            return

        mcp = self._boards[board_idx]
        for p in range(self.CHANNELS):
            mcp.get_pin(p).value = self.active_low

    # ─────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────

    def select(self, board_idx: int, resistance: str) -> dict:
        if board_idx not in self._boards:
            raise ValueError(
                f"Board {board_idx} is invalid or the controller is not open."
            )

        ohms = self._parse_resistance(resistance)
        best = self._closest(ohms)

        self._all_off(board_idx)

        if not self.dry_run:
            # Channel N uses GP(N-1), matching the hardware layout.
            pin = self._boards[board_idx].get_pin(best["channel"] - 1)
            pin.value = not self.active_low

        result = {
            "board": board_idx,
            "channel": best["channel"],
            "label": best["label"],
            "requested_ohms": ohms,
            "nominal_ohms": best["nominal"],
            "error_pct": best["err"] * 100,
        }

        self._selections[board_idx] = result
        return result

    def off(self, board_idx: int) -> None:
        self._all_off(board_idx)
        self._selections[board_idx] = None

    def off_all(self) -> None:
        for b in range(self.num_boards):
            self.off(b)

    def status(self) -> Dict[int, Optional[dict]]:
        return dict(self._selections)
