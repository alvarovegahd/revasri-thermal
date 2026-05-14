import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

outdir = Path("packet_strip")
outdir.mkdir(exist_ok=True)

raw_bytes = open("thermal_dump.bin", "rb").read()

PACKET = 512
H, W = 192, 256
BYTES_PER_PIXEL = 2
FRAME_PIXELS = H * W
FRAME_BYTES = FRAME_PIXELS * 2

print("Testing packet header stripping...")

def try_strip(header_bytes):
    payload = bytearray()

    # split into packets
    for i in range(0, len(raw_bytes), PACKET):
        pkt = raw_bytes[i:i+PACKET]
        if len(pkt) < PACKET:
            continue
        payload.extend(pkt[header_bytes:])

    if len(payload) < FRAME_BYTES:
        return None

    frame = payload[:FRAME_BYTES]
    img = np.frombuffer(frame, dtype='>u2').reshape(H, W)
    img = img & 0x3FFF

    return img

# 🔥 common packet header sizes
candidates = [0, 4, 8, 12, 16, 20, 24, 32]

for hb in candidates:
    img = try_strip(hb)
    if img is None:
        continue

    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(f"packet_header_{hb}")
    plt.savefig(outdir / f"packet_header_{hb}.png", dpi=150)
    plt.close()

print(f"Saved to {outdir}/")

