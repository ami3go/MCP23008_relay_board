"""
resistor_selector_fn.py
=======================
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

Public functions
----------------
    open_controller(num_boards, active_low, dry_run) -> ctrl
    close_controller(ctrl)

    parse_resistance(text)            -> float (Ohms)
    find_closest(resistance)          -> result dict
    select(ctrl, board, resistance)   -> result dict
    deselect(ctrl, board)
    deselect_all(ctrl)
    current_selection(ctrl, board)    -> result dict | None
    all_selections(ctrl)              -> {board: result | None}

    fmt_ohms(ohms)                    -> str
    fmt_result(result)                -> str
    print_resistor_map()
    print_result(result)
    print_all_selections(ctrl)

CLI
---
    python resistor_selector_fn.py                         # interactive
    python resistor_selector_fn.py --board 0 --resistance 13k
    python resistor_selector_fn.py --list
    python resistor_selector_fn.py --off
    python resistor_selector_fn.py --dry-run --resistance 50k
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional
from bms_browser_scripts.Project_data.Equipments.mcp23008_2 import mcp23008_relay_boards



# ── Resistor map ──────────────────────────────────────────────────────────────

# RESISTOR_MAP: dict[int, float] = {
#     1:   1_300,
#     2:   1_600,
#     3:  13_000,
#     4:  30_000,
#     5:  68_000,
#     6: 180_000,
# }
#
# RESISTOR_LABELS: dict[int, str] = {
#     1:  "1.3 kΩ",
#     2:  "1.6 kΩ",
#     3:   "13 kΩ",
#     4:   "30 kΩ",
#     5:   "68 kΩ",
#     6:  "180 kΩ",
# }

RESISTOR_MAP: dict[int, float] = {
    6:   1_300,
    5:   1_600,
    4:  3_000,
    3:  34_000,
    2:  68_000,
    1: 180_000,
}

RESISTOR_LABELS: dict[int, str] = {
    6:  "1.3 kΩ",
    5:  "1.6 kΩ",
    4:   "3 kΩ",
    3:   "34 kΩ",
    2:   "68 kΩ",
    1:  "180 kΩ",
}


NUM_BOARDS_DEFAULT = 5


# ─────────────────────────────────────────────────────────────────────────────
# Pure / stateless functions
# ─────────────────────────────────────────────────────────────────────────────

def fmt_ohms(ohms: float) -> str:
    """Format a resistance value as a human-readable string."""
    if ohms >= 1_000_000:
        return f"{ohms / 1_000_000:.3g} MΩ"
    if ohms >= 1_000:
        return f"{ohms / 1_000:.4g} kΩ"
    return f"{ohms:.4g} Ω"


def parse_resistance(text: str) -> float:
    """
    Parse a resistance string to Ohms (float).

    Accepts: '13k', '1.3K', '68000', '180k', '1.5M', '30 kΩ', '30kohm', etc.

    Raises ValueError on unrecognised input.
    """
    s = (
        text.strip()
        .replace(" ", "")
        .replace("Ω", "")
        .replace("ohm", "")
        .replace("OHM", "")
    )
    multiplier = 1.0
    if s and s[-1].lower() == "k":
        multiplier = 1_000.0
        s = s[:-1]
    elif s and s[-1].lower() == "m":
        multiplier = 1_000_000.0
        s = s[:-1]
    try:
        return float(s) * multiplier
    except ValueError:
        raise ValueError(f"Cannot parse resistance: '{text}'")


def find_closest(resistance: float | str) -> dict:
    """
    Return a result dict for the RESISTOR_MAP channel whose nominal value
    is closest to *resistance* (by minimum relative error).

    Does NOT touch any hardware.

    Parameters
    ----------
    resistance : float | str
        Target in Ohms, or a string like '13k'.

    Returns
    -------
    dict with keys:
        channel       int   – 1-based channel number
        nominal_ohms  float – nominal resistor value
        requested_ohms float
        error_pct     float – signed relative error (%)
        label         str   – e.g. '13 kΩ'
        board         None  – not yet assigned
        switched      bool  – False (no hardware touched)
    """
    ohms = parse_resistance(str(resistance)) if isinstance(resistance, str) else float(resistance)

    best_ch  = -1
    best_nom = 0.0
    best_err = float("inf")

    for ch, nom in RESISTOR_MAP.items():
        err = abs(nom - ohms) / ohms * 100.0
        if err < best_err:
            best_err = err
            best_ch  = ch
            best_nom = nom

    return {
        "channel":        best_ch,
        "nominal_ohms":   best_nom,
        "requested_ohms": ohms,
        "error_pct":      (best_nom - ohms) / ohms * 100.0,
        "label":          RESISTOR_LABELS[best_ch],
        "board":          None,
        "switched":       False,
    }


def fmt_result(result: dict) -> str:
    """One-line string representation of a result dict."""
    state = "SWITCHED" if result.get("switched") else "FOUND (dry-run)"
    board_str = f"Board {result['board']}  " if result.get("board") is not None else ""
    return (
        f"[{state}] {board_str}"
        f"Channel {result['channel']}  {result['label']}  "
        f"(requested {fmt_ohms(result['requested_ohms'])},  "
        f"error {result['error_pct']:+.1f}%)"
    )


def print_result(result: dict) -> None:
    """Print a result dict, with a warning when error exceeds 20 %."""
    print(f"\n  {fmt_result(result)}")
    if abs(result["error_pct"]) > 20:
        print(
            f"  ⚠  Large error ({result['error_pct']:+.1f}%) – "
            f"closest available is {result['label']} "
            f"({result['nominal_ohms']:,.0f} Ω)"
        )
    print()


def print_resistor_map() -> None:
    """Print the channel → resistor mapping table."""
    print()
    print("  Channel → Resistor map")
    print("  " + "─" * 38)
    print(f"  {'Channel':<10} {'Nominal':<14} {'Value (Ω)'}")
    print("  " + "─" * 38)
    for ch, nom in RESISTOR_MAP.items():
        print(f"  {ch:<10} {RESISTOR_LABELS[ch]:<14} {nom:>10,.0f}")
    print()


def print_all_selections(ctrl: dict) -> None:
    """Print current relay selection for every board in *ctrl*."""
    any_active = False
    for b in range(ctrl["num_boards"]):
        r = current_selection(ctrl, b)
        if r:
            print(f"  Board {b}: channel {r['channel']}  {r['label']}")
            any_active = True
    if not any_active:
        print("  All boards idle (no relay selected).")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Controller lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def open_controller(
    num_boards: int = NUM_BOARDS_DEFAULT,
    active_low: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Open the relay hardware and return a controller handle (plain dict).

    The handle is the single piece of mutable state in this module.
    Pass it to every stateful function (select, deselect, …).

    Parameters
    ----------
    num_boards : int   Number of relay boards (1–5).
    active_low : bool  True if your relay fires when output is LOW.
    dry_run    : bool  If True, calculate only – never touch hardware.

    Returns
    -------
    dict with keys:
        relay_ctrl   RelayController | None
        num_boards   int
        dry_run      bool
        selections   {board_index: result_dict | None}
    """
    relay_ctrl = None

    if not dry_run:
        if "BLINKA_FT232H" not in os.environ:
            os.environ["BLINKA_FT232H"] = "1"
        try:
            # from mcp23008_relay_boards import RelayController  # type: ignore
            from bms_browser_scripts.Project_data.Equipments.mcp23008_2.mcp23008_relay_boards import RelayController
            relay_ctrl = RelayController(num_boards=num_boards, active_low=active_low)
        except Exception as exc:
            raise RuntimeError(
                f"Could not open relay hardware: {exc}\n"
                "  Hint: pass dry_run=True to test without hardware."
            ) from exc

    return {
        "relay_ctrl": relay_ctrl,
        "num_boards": num_boards,
        "dry_run":    dry_run,
        "selections": {i: None for i in range(num_boards)},
    }


def close_controller(ctrl: dict) -> None:
    """Release I2C bus and hardware resources."""
    rc = ctrl.get("relay_ctrl")
    if rc is not None:
        try:
            rc.close()
        except Exception:
            pass
    ctrl["relay_ctrl"] = None


# ─────────────────────────────────────────────────────────────────────────────
# Stateful relay functions  (require a controller handle)
# ─────────────────────────────────────────────────────────────────────────────

def _check_board(ctrl: dict, board: int) -> None:
    n = ctrl["num_boards"]
    if not 0 <= board < n:
        raise ValueError(f"board must be 0–{n - 1}, got {board}")


def select(ctrl: dict, board: int, resistance: float | str) -> dict:
    """
    Switch the relay whose resistor best matches *resistance* on *board*.

    Turns all other channels on that board OFF first so only one
    resistor is connected at a time.

    Parameters
    ----------
    ctrl       : dict  Handle from open_controller().
    board      : int   Board index (0-based).
    resistance : float | str  Target Ω or string ('13k', '68000', …).

    Returns
    -------
    result dict (same schema as find_closest(), with board and switched set).
    """
    _check_board(ctrl, board)

    result = find_closest(resistance)
    result["board"]    = board
    result["switched"] = not ctrl["dry_run"]

    rc = ctrl["relay_ctrl"]
    if not ctrl["dry_run"] and rc is not None:
        rc.all_off(board)
        rc.on(board, result["channel"])

    ctrl["selections"][board] = result
    return result


def deselect(ctrl: dict, board: int) -> None:
    """Turn off all relays on *board*."""
    _check_board(ctrl, board)
    ctrl["selections"][board] = None
    rc = ctrl["relay_ctrl"]
    if not ctrl["dry_run"] and rc is not None:
        rc.all_off(board)


def deselect_all(ctrl: dict) -> None:
    """Turn off every relay on every board."""
    for b in range(ctrl["num_boards"]):
        ctrl["selections"][b] = None
    rc = ctrl["relay_ctrl"]
    if not ctrl["dry_run"] and rc is not None:
        rc.all_boards_off()


def current_selection(ctrl: dict, board: int) -> Optional[dict]:
    """Return the last selected result dict for *board*, or None."""
    _check_board(ctrl, board)
    return ctrl["selections"].get(board)


def all_selections(ctrl: dict) -> dict[int, Optional[dict]]:
    """Return {board_index: result_dict | None} for all boards."""
    return dict(ctrl["selections"])


# ─────────────────────────────────────────────────────────────────────────────
# Interactive CLI
# ─────────────────────────────────────────────────────────────────────────────

_HELP = """
  Commands
  ────────────────────────────────────────────────────
  <resistance> [board]     Select relay, e.g. '13k', '68000 2'
  off [board|all]          Turn off a board or all boards
  status                   Show current selections
  list                     Show channel → resistor map
  help                     Show this message
  quit / exit / q          Exit
  ────────────────────────────────────────────────────
"""


def _interactive(ctrl: dict) -> None:
    num_boards = ctrl["num_boards"]
    dry_tag    = "  [DRY-RUN – no hardware]" if ctrl["dry_run"] else ""

    print()
    print("═" * 56)
    print("  Resistor Selector  –  Interactive Mode" + dry_tag)
    print("═" * 56)
    print(_HELP)

    while True:
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Exiting.")
            break

        if not raw:
            continue

        parts = raw.split()
        cmd   = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            break

        if cmd == "help":
            print(_HELP)
            continue

        if cmd == "list":
            print_resistor_map()
            continue

        if cmd == "status":
            print_all_selections(ctrl)
            continue

        if cmd == "off":
            target = parts[1].lower() if len(parts) > 1 else "0"
            if target == "all":
                deselect_all(ctrl)
                print("  All boards OFF.\n")
            else:
                try:
                    deselect(ctrl, int(target))
                    print(f"  Board {target} OFF.\n")
                except (ValueError, IndexError) as e:
                    print(f"  Error: {e}\n")
            continue

        # Default: treat first token as resistance, optional second as board
        board_str = parts[1] if len(parts) > 1 else "0"
        try:
            board = int(board_str)
        except ValueError:
            print(f"  Invalid board '{board_str}'. Use a number 0–{num_boards - 1}.\n")
            continue

        try:
            result = select(ctrl, board, cmd)
            print_result(result)
        except (ValueError, RuntimeError) as e:
            print(f"  Error: {e}\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Resistor selector – switch relays by resistance value (functional API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python resistor_selector_fn.py                         # interactive REPL
  python resistor_selector_fn.py -b 0 -r 13k
  python resistor_selector_fn.py -b 2 -r 68000
  python resistor_selector_fn.py -r 1.5k --dry-run
  python resistor_selector_fn.py --list
  python resistor_selector_fn.py --off
        """,
    )
    parser.add_argument("--board",      "-b", type=int,  default=None, help="Board index (0-based, default: 0)")
    parser.add_argument("--resistance", "-r", type=str,  default=None, help="Target resistance, e.g. '13k' or '68000'")
    parser.add_argument("--boards",     "-n", type=int,  default=5,    help="Total number of boards (default: 5)")
    parser.add_argument("--active-low",       action="store_true",     help="Relay fires on LOW output")
    parser.add_argument("--dry-run",          action="store_true",     help="Calculate only, do not switch hardware")
    parser.add_argument("--list",             action="store_true",     help="Print channel→resistor map and exit")
    parser.add_argument("--off",              action="store_true",     help="Turn off all relays and exit")

    args = parser.parse_args(argv)

    # ── list (no hardware needed) ─────────────────────────────────────────────
    if args.list:
        print_resistor_map()
        return

    # ── open hardware (or dry-run stub) ───────────────────────────────────────
    try:
        ctrl = open_controller(
            num_boards=args.boards,
            active_low=args.active_low,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)

    try:
        # ── --off ─────────────────────────────────────────────────────────────
        if args.off:
            deselect_all(ctrl)
            print("  All boards OFF.")
            return

        # ── one-shot mode ─────────────────────────────────────────────────────
        if args.resistance is not None:
            board = args.board if args.board is not None else 0
            try:
                result = select(ctrl, board, args.resistance)
                print_result(result)
            except (ValueError, RuntimeError) as exc:
                print(f"[ERROR] {exc}")
                sys.exit(1)
            return

        # ── interactive REPL ──────────────────────────────────────────────────
        _interactive(ctrl)

    finally:
        close_controller(ctrl)


if __name__ == "__main__":
    main()