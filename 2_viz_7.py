import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

outdir = Path("bitphase_search")
outdir.mkdir(exist_ok=True)

raw = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
PIXELS = H * W

bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))

print("Searching bit phases...")

def try_phase(phase):
    usable = bits[phase:]
    usable = usable[: (len(usable)//14)*14]

    if len(usable) < PIXELS*14:
        return None

    pixbits = usable.reshape(-1,14)
    vals = pixbits.dot(1 << np.arange(13, -1, -1))

    img = vals[:PIXELS].reshape(H,W)
    return img

# try all possible bit shifts
for phase in range(14):
    img = try_phase(phase)
    if img is None:
        continue

    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(f"phase_{phase}")
    plt.savefig(outdir / f"phase_{phase}.png", dpi=150)
    plt.close()

print("Saved phase search images.")

