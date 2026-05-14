import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

img = np.load("frame_rows.npy")
H, W = img.shape

outdir = Path("blockfix")
outdir.mkdir(exist_ok=True)

def save(im, name):
    plt.figure(figsize=(4,3))
    plt.imshow(im, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=140)
    plt.close()

print("Running block deinterleave search...")

# 🔥 test likely macroblock sizes
for block in [16, 32, 64]:
    if W % block != 0:
        continue

    nblocks = W // block

    for k in [2,4,8,16]:
        if block % k != 0:
            continue

        try:
            reshaped = img.reshape(H, nblocks, k, block//k)

            # local deinterleave inside each block
            fixed = reshaped.transpose(0,1,3,2).reshape(H, W)

            save(fixed, f"block{block}_k{k}")

        except Exception:
            pass

print("Done.")

