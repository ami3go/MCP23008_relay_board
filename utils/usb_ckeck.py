import usb.core

import usb.backend.libusb1
backend = usb.backend.libusb1.get_backend()
print(backend)
# backend = usb.backend.libusb1.get_backend(
#     find_library=lambda x: "libusb-1.0.dll"
# )
#
# print(backend)

# print(dev)