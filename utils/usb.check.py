from __future__ import annotations
import os
from typing import Dict, Optional

if "BLINKA_FT232H" not in os.environ:
    os.environ["BLINKA_FT232H"] = "1"
    os.environ["PYUSB_BACKEND"] = "libusb1"
import board
import busio
from adafruit_mcp230xx.mcp23008 import MCP23008

import usb.backend.libusb1
# then pass backend=backend when creating the device handle
print(usb.backend.libusb1.get_backend())