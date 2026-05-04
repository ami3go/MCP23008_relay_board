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

Dependencies
------------
    pip install adafruit-blinka adafruit-circuitpython-mcp230xx

Environment variable required by Blinka for FT232H:
    Windows / Linux / macOS:
        set BLINKA_FT232H=1   (Windows)
        export BLINKA_FT232H=1  (Linux/macOS)

Usage example
-------------
    import os
    os.environ["BLINKA_FT232H"] = "1"   # must be set before importing board

    from relay_board import RelayController

    ctrl = RelayController(num_boards=5, active_low=False)

    ctrl.on(board=0, channel=1)        # turn on board 0, relay 1
    ctrl.off(board=2, channel=5)       # turn off board 2, relay 5
    ctrl.toggle(board=1, channel=3)    # toggle board 1, relay 3
    ctrl.set(board=0, channel=4, state=True)

    # All channels on a board at once
    ctrl.all_on(board=0)
    ctrl.all_off(board=1)

    # Set all 8 channels from a bitmask (bit0 = channel1, bit7 = channel8)
    ctrl.set_mask(board=0, mask=0b10101010)

    # Read current state
    print(ctrl.get_state(board=0))     # returns dict {1: True, 2: False, ...}
    print(ctrl.get_channel(board=0, channel=1))  # True / False

    ctrl.close()
"""

from __future__ import annotations

import os
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Optional: auto-set BLINKA_FT232H if not already set
# ---------------------------------------------------------------------------
if "BLINKA_FT232H" not in os.environ:
    os.environ["BLINKA_FT232H"] = "1"

import board                                          # type: ignore  (blinka)
import busio                                          # type: ignore  (blinka)
from adafruit_mcp230xx.mcp23008 import MCP23008       # type: ignore
from digitalio import Direction                       # type: ignore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MCP23008_BASE_ADDR = 0x20
MAX_BOARDS = 5
CHANNELS_PER_BOARD = 8


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _board_addr(board_index: int) -> int:
    """Return the I2C address for the given board index (0-based)."""
    if not 0 <= board_index < MAX_BOARDS:
        raise ValueError(f"board_index must be 0-{MAX_BOARDS - 1}, got {board_index}")
    return MCP23008_BASE_ADDR + board_index


def _check_channel(channel: int) -> None:
    if not 1 <= channel <= CHANNELS_PER_BOARD:
        raise ValueError(f"channel must be 1-{CHANNELS_PER_BOARD}, got {channel}")


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class RelayBoard:
    """Represents a single MCP23008-based 8-channel relay board."""

    def __init__(self, i2c: busio.I2C, board_index: int, active_low: bool = False):
        self._active_low = active_low
        self._index = board_index
        addr = _board_addr(board_index)
        self._mcp = MCP23008(i2c, address=addr)

        # Configure all pins as outputs, de-energised initially
        self._pins = []
        for pin_num in range(CHANNELS_PER_BOARD):
            pin = self._mcp.get_pin(pin_num)
            pin.direction = Direction.OUTPUT
            pin.value = True if active_low else False   # relay OFF
            self._pins.append(pin)

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _relay_on_value(self) -> bool:
        """Logic level that energises the relay coil."""
        return False if self._active_low else True

    def _relay_off_value(self) -> bool:
        return True if self._active_low else False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on(self, channel: int) -> None:
        """Energise relay on the given channel (1-based)."""
        _check_channel(channel)
        self._pins[channel - 1].value = self._relay_on_value()

    def off(self, channel: int) -> None:
        """De-energise relay on the given channel (1-based)."""
        _check_channel(channel)
        self._pins[channel - 1].value = self._relay_off_value()

    def toggle(self, channel: int) -> None:
        """Toggle relay on the given channel (1-based)."""
        _check_channel(channel)
        pin = self._pins[channel - 1]
        pin.value = not pin.value

    def set(self, channel: int, state: bool) -> None:
        """Set relay to an explicit state (True = ON, False = OFF)."""
        if state:
            self.on(channel)
        else:
            self.off(channel)

    def all_on(self) -> None:
        """Energise all 8 relays."""
        val = self._relay_on_value()
        for pin in self._pins:
            pin.value = val

    def all_off(self) -> None:
        """De-energise all 8 relays."""
        val = self._relay_off_value()
        for pin in self._pins:
            pin.value = val

    def set_mask(self, mask: int) -> None:
        """
        Set all 8 channels from an 8-bit mask.
        Bit 0 (LSB) corresponds to channel 1, bit 7 to channel 8.
        A set bit means ON.
        """
        if not 0 <= mask <= 0xFF:
            raise ValueError("mask must be an 8-bit value (0x00–0xFF)")
        on_val = self._relay_on_value()
        off_val = self._relay_off_value()
        for i, pin in enumerate(self._pins):
            pin.value = on_val if (mask >> i) & 1 else off_val

    def get_state(self) -> Dict[int, bool]:
        """Return current state of all relays as {channel: is_on}."""
        on_val = self._relay_on_value()
        return {i + 1: (self._pins[i].value == on_val) for i in range(CHANNELS_PER_BOARD)}

    def get_channel(self, channel: int) -> bool:
        """Return True if the relay is currently ON, False if OFF."""
        _check_channel(channel)
        return self._pins[channel - 1].value == self._relay_on_value()


# ---------------------------------------------------------------------------
# Controller – manages all boards
# ---------------------------------------------------------------------------

class RelayController:
    """
    High-level controller for multiple MCP23008-based relay boards
    connected to an FT232H via I2C.

    Parameters
    ----------
    num_boards : int
        Number of relay boards to manage (1–5).
    active_low : bool
        Set True if your relay module energises when the output is LOW.
    scl_pin : optional
        Override the SCL pin (defaults to board.SCL for FT232H).
    sda_pin : optional
        Override the SDA pin (defaults to board.SDA for FT232H).
    frequency : int
        I2C clock frequency in Hz (default 100 000).
    """

    def __init__(
        self,
        num_boards: int = 5,
        active_low: bool = False,
        scl_pin=None,
        sda_pin=None,
        frequency: int = 100_000,
    ):
        if not 1 <= num_boards <= MAX_BOARDS:
            raise ValueError(f"num_boards must be 1-{MAX_BOARDS}")

        scl = scl_pin if scl_pin is not None else board.SCL
        sda = sda_pin if sda_pin is not None else board.SDA
        self._i2c = busio.I2C(scl, sda, frequency=frequency)

        self._boards: Dict[int, RelayBoard] = {}
        for idx in range(num_boards):
            self._boards[idx] = RelayBoard(self._i2c, idx, active_low=active_low)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_board(self, board: int) -> RelayBoard:
        if board not in self._boards:
            raise ValueError(f"Board {board} is not managed by this controller")
        return self._boards[board]

    # ------------------------------------------------------------------
    # Per-channel control
    # ------------------------------------------------------------------

    def on(self, board: int, channel: int) -> None:
        """Turn ON a single relay. board is 0-based, channel is 1-based."""
        self._get_board(board).on(channel)

    def off(self, board: int, channel: int) -> None:
        """Turn OFF a single relay."""
        self._get_board(board).off(channel)

    def toggle(self, board: int, channel: int) -> None:
        """Toggle a single relay."""
        self._get_board(board).toggle(channel)

    def set(self, board: int, channel: int, state: bool) -> None:
        """Explicitly set a relay state (True=ON, False=OFF)."""
        self._get_board(board).set(channel, state)

    # ------------------------------------------------------------------
    # Whole-board control
    # ------------------------------------------------------------------

    def all_on(self, board: int) -> None:
        """Energise all 8 relays on a board."""
        self._get_board(board).all_on()

    def all_off(self, board: int) -> None:
        """De-energise all 8 relays on a board."""
        self._get_board(board).all_off()

    def set_mask(self, board: int, mask: int) -> None:
        """
        Set all 8 channels on a board from a bitmask.
        Bit 0 = channel 1, bit 7 = channel 8. Set bit = ON.
        """
        self._get_board(board).set_mask(mask)

    # ------------------------------------------------------------------
    # Global control
    # ------------------------------------------------------------------

    def all_boards_on(self) -> None:
        """Energise every relay on every board."""
        for b in self._boards.values():
            b.all_on()

    def all_boards_off(self) -> None:
        """De-energise every relay on every board."""
        for b in self._boards.values():
            b.all_off()

    # ------------------------------------------------------------------
    # State reading
    # ------------------------------------------------------------------

    def get_state(self, board: int) -> Dict[int, bool]:
        """Return {channel: is_on} for every channel on a board."""
        return self._get_board(board).get_state()

    def get_channel(self, board: int, channel: int) -> bool:
        """Return True if a specific relay is ON."""
        return self._get_board(board).get_channel(channel)

    def get_all_states(self) -> Dict[int, Dict[int, bool]]:
        """Return {board_index: {channel: is_on}} for all managed boards."""
        return {idx: b.get_state() for idx, b in self._boards.items()}

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the I2C bus."""
        try:
            self._i2c.deinit()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()