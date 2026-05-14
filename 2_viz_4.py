import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

outdir = Path("packed_test")
outdir.mkdir(exist_ok=True)

raw_bytes = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
FRAME_BYTES = H * W * 2

frame = raw_bytes[:FRAME_BYTES]

# interpret as big endian first
img_be = np.frombuffer(frame, dtype='>u2').reshape(H, W)

# 🔥 mask to 14 bits (very common thermal format)
img_14 = img_be & 0x3FFF

plt.figure(figsize=(4,3))
plt.imshow(img_14, cmap="inferno")
plt.colorbar()
plt.title("masked_14bit")
plt.savefig(outdir / "masked_14bit.png", dpi=150)
plt.close()

print(f"Saved to {outdir}/")

