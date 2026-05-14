import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

raw = Path("thermal_dump.bin").read_bytes()

PACKET = 512
H, W = 192, 256

outdir = Path("stride_search")
outdir.mkdir(exist_ok=True)

def save(img, name):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=120)
    plt.close()

# ---------- build row-stacked image first ----------
rows = []

for i in range(H):
    pkt = raw[i*PACKET:(i+1)*PACKET]
    if len(pkt) < PACKET:
        break

    payload = pkt  # skip=0 best so far
    row = np.frombuffer(payload[:W*2], dtype=">u2") & 0x3FFF
    rows.append(row)

img = np.stack(rows[:H], axis=0)
save(img, "baseline_rows")

print("Running stride demux...")

# ---------- stride tests ----------
for k in [2, 4, 8, 16]:
    if W % k != 0:
        continue

    out = np.zeros_like(img)

    # deinterleave by stride
    for i in range(k):
        out[:, i::k] = img[:, i*(W//k):(i+1)*(W//k)]

    save(out, f"stride{k}")

print("Done.")

