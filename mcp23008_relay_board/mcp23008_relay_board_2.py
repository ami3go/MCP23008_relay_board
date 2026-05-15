from __future__ import annotations
import logging
import os
from typing import Dict, Optional

if "BLINKA_FT232H" not in os.environ:
    os.environ["BLINKA_FT232H"] = "1"
    os.environ["PYUSB_BACKEND"] = "libusb1"
import board
import busio
from adafruit_mcp230xx.mcp23008 import MCP23008

log = logging.getLogger(__name__)

class ResistorSelector:
    MCP_BASE   = 0x20
    MAX_BOARDS = 5
    CHANNELS   = 8

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

    # ------------------------------------------------------------
    def __init__(self, num_boards: int = 5, active_low: bool = False,
                 dry_run: bool = False):
        if not 1 <= num_boards <= self.MAX_BOARDS:
            raise ValueError("num_boards must be 1–5")
        self.num_boards = num_boards
        self.active_low = active_low
        self.dry_run = dry_run
        self._i2c: Optional[busio.I2C] = None
        self._boards: Dict[int, MCP23008] = {}
        self._selections: Dict[int, Optional[dict]] = {}

    # ------------------------------------------------------------
    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ------------------------------------------------------------
    def open(self) -> None:
        if self._i2c is not None:
            return
        self._i2c = busio.I2C(board.SCL, board.SDA)

        for i in range(self.num_boards):
            addr = self.MCP_BASE + i
            mcp = MCP23008(self._i2c, address=addr)

            # Set all pins to output in a single write
            mcp.iodir = 0x00

            # Set initial relay state (all off) in a single write
            mcp.gpio = 0xFF if self.active_low else 0x00

            self._boards[i] = mcp
            self._selections[i] = None

    # ------------------------------------------------------------
    def close(self) -> None:
        # Turn everything off with per‑board error isolation
        if not self.dry_run:
            for b in range(self.num_boards):
                try:
                    self._all_off(b)
                except Exception as exc:
                    log.warning("close(): could not turn off board %d: %s", b, exc)

        self._boards.clear()
        self._selections.clear()

        if self._i2c is not None:
            try:
                self._i2c.deinit()
            except Exception as exc:
                log.warning("close(): I2C deinit failed: %s", exc)
            finally:
                self._i2c = None

    # ------------------------------------------------------------
    # Helper: single GPIO write to turn all relays off
    # ------------------------------------------------------------
    def _all_off(self, board_idx: int) -> None:
        if self.dry_run:
            return
        mcp = self._boards[board_idx]
        # One I2C write instead of eight
        mcp.gpio = 0xFF if self.active_low else 0x00

    # ------------------------------------------------------------
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
        return best  # map is never empty

    # ------------------------------------------------------------
    # Public API – all now single I2C transaction per operation
    # ------------------------------------------------------------
    def select(self, board_idx: int, resistance: str) -> dict:
        if board_idx not in self._boards:
            raise ValueError("Board not available. Is the controller open?")

        ohms = self._parse_resistance(resistance)
        best = self._closest(ohms)
        pin_index = best["channel"] - 1   # 0‑based

        # Build a byte that has ONLY that relay active.
        # For active_low = False: relay ON  → pin HIGH (1)
        # For active_low = True : relay ON  → pin LOW  (0)
        if self.active_low:
            # All pins high, except the chosen one
            gpio_byte = 0xFF & ~(1 << pin_index)
        else:
            # Single pin high, rest low
            gpio_byte = 1 << pin_index

        if not self.dry_run:
            try:
                self._boards[board_idx].gpio = gpio_byte
            except Exception as exc:
                log.error("select() I2C write failed: %s", exc)
                # Optionally try to close and re‑open the bus here.
                # For now, raise a cleaner exception.
                raise RuntimeError("Failed to set relay on board %d: %s" %
                                   (board_idx, exc)) from exc

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
        try:
            self._all_off(board_idx)
        except Exception as exc:
            log.warning("off() failed for board %d: %s", board_idx, exc)
            # You may choose to raise after logging
        self._selections[board_idx] = None

    def off_all(self) -> None:
        for b in range(self.num_boards):
            self.off(b)

    def status(self) -> Dict[int, Optional[dict]]:
        return dict(self._selections)