import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

outdir = Path("column_search")
outdir.mkdir(exist_ok=True)

raw = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
FRAME_BYTES = H * W * 2

frame = raw[:FRAME_BYTES]

# use the best interpretation so far
img = np.frombuffer(frame, dtype='>u2').reshape(H, W)
img = img & 0x3FFF

print("Running column interleave search...")

def save(img, name):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=150)
    plt.close()

# baseline
save(img, "baseline")

# 🔥 try common column permutations
for k in [2, 4, 8, 16]:
    if W % k != 0:
        continue

    reshaped = img.reshape(H, k, W//k)

    # try all permutations of the k streams
    import itertools
    perms = list(itertools.permutations(range(k)))

    for p in perms[:min(len(perms), 24)]:  # cap but still large
        test = reshaped[:, p, :].reshape(H, W)
        save(test, f"colperm_k{k}_{'_'.join(map(str,p))}")

print("Column search done.")

