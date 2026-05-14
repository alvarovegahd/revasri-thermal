import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

outdir = Path("endian_test")
outdir.mkdir(exist_ok=True)

raw_bytes = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
FRAME_BYTES = H * W * 2

frame = raw_bytes[:FRAME_BYTES]

# interpret both ways
img_le = np.frombuffer(frame, dtype='<u2').reshape(H, W)
img_be = np.frombuffer(frame, dtype='>u2').reshape(H, W)

def save_img(img, name):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=150)
    plt.close()

save_img(img_le, "little_endian")
save_img(img_be, "big_endian")

print(f"Saved to {outdir}/")

