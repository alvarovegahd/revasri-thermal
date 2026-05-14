import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

raw_bytes = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
FRAME_BYTES = H * W * 2

outdir = Path("offset_search")
outdir.mkdir(exist_ok=True)

scores = []

print("Scanning offsets...")

for offset in range(0, 512):
    usable = raw_bytes[offset:]

    if len(usable) < FRAME_BYTES:
        continue

    frame = usable[:FRAME_BYTES]
    img = np.frombuffer(frame, dtype=np.uint16).reshape(H, W)

    # heuristic: real thermal images are spatially smooth
    # compute neighbor difference energy
    diff_h = np.mean(np.abs(np.diff(img, axis=1)))
    diff_v = np.mean(np.abs(np.diff(img, axis=0)))
    score = diff_h + diff_v

    scores.append((score, offset))

# sort best candidates (lowest = smoother = more image-like)
scores.sort()

print("Top candidate offsets:")
for s in scores[:10]:
    print(s)

# 🔥 save top 5 candidates as images
for _, offset in scores[:5]:
    usable = raw_bytes[offset:]
    frame = usable[:FRAME_BYTES]
    img = np.frombuffer(frame, dtype=np.uint16).reshape(H, W)

    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(f"Offset {offset}")
    plt.savefig(outdir / f"offset_{offset}.png", dpi=150)
    plt.close()

print(f"Saved candidates to {outdir}/")

