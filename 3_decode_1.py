import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

raw = Path("thermal_dump.bin").read_bytes()

PACKET = 512
H, W = 192, 256

outdir = Path("row_probe")
outdir.mkdir(exist_ok=True)

def save(img, name):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(outdir / f"{name}.png", dpi=120)
    plt.close()

# take first N packets and stack as rows
NROWS = H

rows = []

for i in range(NROWS):
    pkt = raw[i*PACKET:(i+1)*PACKET]
    if len(pkt) < PACKET:
        break

    # try several header skips
    for skip in [0, 2, 4, 8, 12, 16]:
        payload = pkt[skip:]

        if len(payload) < W*2:
            continue

        row = np.frombuffer(payload[:W*2], dtype=">u2") & 0x3FFF
        rows.append((skip, row))

# group by skip and build images
from collections import defaultdict
by_skip = defaultdict(list)

idx = 0
for i in range(NROWS):
    pkt = raw[i*PACKET:(i+1)*PACKET]
    if len(pkt) < PACKET:
        break

    for skip in [0, 2, 4, 8, 12, 16]:
        payload = pkt[skip:]
        if len(payload) < W*2:
            continue
        row = np.frombuffer(payload[:W*2], dtype=">u2") & 0x3FFF
        by_skip[skip].append(row)

for skip, rlist in by_skip.items():
    if len(rlist) >= H:
        img = np.stack(rlist[:H], axis=0)
        save(img, f"rows_skip{skip}")

print("Row probe done.")

