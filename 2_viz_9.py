import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import itertools
import gc

outdir = Path("column_search")
outdir.mkdir(exist_ok=True)

raw = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
FRAME_BYTES = H * W * 2

frame = raw[:FRAME_BYTES]

img = np.frombuffer(frame, dtype='>u2').reshape(H, W)
img = img & 0x3FFF

print("Running column interleave search (RAM-safe)...")

def save(img, name):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=120)
    plt.close("all")        # 🔥 important
    gc.collect()            # 🔥 important

# baseline
save(img, "baseline")

MAX_PER_K = 32   # you can increase later

for k in [2, 4, 8, 16]:
    if W % k != 0:
        continue

    print(f"Testing k={k}")

    reshaped = img.reshape(H, k, W//k)

    perm_iter = itertools.permutations(range(k))

    for idx, p in enumerate(perm_iter):
        if idx >= MAX_PER_K:
            break

        test = reshaped[:, p, :].reshape(H, W)
        save(test, f"colperm_k{k}_{idx}")

print("Column search done.")

