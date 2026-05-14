import usb.core
import usb.util
import numpy as np

dev = usb.core.find(idVendor=0x04b4, idProduct=0x000a)
assert dev is not None, "Device not found"

# ⭐ Only set configuration if needed
try:
    dev.set_configuration()
except usb.core.USBError as e:
    if e.errno == 16:
        print("Device already configured — continuing")
    else:
        raise

# ⭐ ensure correct altsetting
try:
    dev.set_interface_altsetting(interface=0, alternate_setting=1)
except usb.core.USBError:
    pass

cfg = dev.get_active_configuration()
intf = cfg[(0,1)]

ep_in = usb.util.find_descriptor(
    intf,
    custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_IN
)

# collect data
buf = bytearray()

for _ in range(200):
    try:
        data = ep_in.read(512, timeout=2000)
        buf.extend(data)
    except Exception as e:
        print("read error:", e)
        break

print("Total bytes:", len(buf))

with open("thermal_dump.bin", "wb") as f:
    f.write(buf)

print("Saved thermal_dump.bin")

