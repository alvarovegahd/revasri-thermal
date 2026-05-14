import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# load your already reconstructed frame
img = np.load("frame_rows.npy") if Path("frame_rows.npy").exists() else None

# if you didn't save it yet, rebuild quickly from previous script output
if img is None:
    print("Please save frame_rows.npy from previous step.")
    raise SystemExit

H, W = img.shape
outdir = Path("colfix")
outdir.mkdir(exist_ok=True)

def save(im, name):
    plt.figure(figsize=(4,3))
    plt.imshow(im, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=140)
    plt.close()

# try common interleave factors
for k in [2,4,8,16,32]:
    if W % k != 0:
        continue

    reshaped = img.reshape(H, k, W//k)

    # canonical deinterleave
    fixed = reshaped.transpose(0,2,1).reshape(H, W)

    save(fixed, f"deinterleave_k{k}")

print("Done.")

