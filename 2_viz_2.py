import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

raw_bytes = open("thermal_dump.bin", "rb").read()

H, W = 192, 256
BYTES_PER_PIXEL = 2

outdir = Path("header_search")
outdir.mkdir(exist_ok=True)

results = []

def reconstruct(header_bytes):
    row_stride = header_bytes + W * BYTES_PER_PIXEL
    frame_bytes = row_stride * H

    if len(raw_bytes) < frame_bytes:
        return None, None

    frame = raw_bytes[:frame_bytes]

    rows = []
    idx = 0
    for _ in range(H):
        row = frame[idx + header_bytes : idx + row_stride]
        rows.append(row)
        idx += row_stride

    img = np.frombuffer(b''.join(rows), dtype=np.uint16).reshape(H, W)

    # smoothness heuristic (lower is better)
    diff_h = np.mean(np.abs(np.diff(img, axis=1)))
    diff_v = np.mean(np.abs(np.diff(img, axis=0)))
    score = diff_h + diff_v

    return img, score

print("Scanning header sizes...")

for hb in [0, 2, 4, 8, 12, 16, 24, 32, 48, 64]:
    img, score = reconstruct(hb)
    if img is None:
        continue
    results.append((score, hb))
    print(f"header {hb:2d} → score {score:.2f}")

# sort best candidates
results.sort()

print("\nTop candidates:")
for s in results[:5]:
    print(s)

# 🔥 save best 5 images
for score, hb in results[:5]:
    img, _ = reconstruct(hb)

    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(f"Header {hb} bytes | score {score:.2f}")
    plt.savefig(outdir / f"header_{hb}_score_{int(score)}.png", dpi=150)
    plt.close()

print(f"\nSaved candidates to {outdir}/")

