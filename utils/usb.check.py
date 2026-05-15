import usb.backend.libusb1
backend = usb.backend.libusb1.get_backend()
# then pass backend=backend when creating the device handle
print(backend.instance)