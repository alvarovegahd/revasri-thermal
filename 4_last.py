import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import re

RAW_PATH = "thermal_dump.bin"
OUT_PNG = "reassembled.png"

H, W = 192, 256
ROW_BYTES = W * 2
MARKER = b"\x0a\x0a\x00\x01"

raw = Path(RAW_PATH).read_bytes()

# find all marker positions
hits = [m.start() for m in re.finditer(MARKER, raw)]
print("marker hits:", len(hits))
print("first hits:", hits[:10])

def best_header_len(seg: bytes):
    """
    Choose a header length that makes remainder a whole number of 512-byte rows.
    Prefer 12 if it fits (because your 14348 block strongly indicates that).
    """
    cands = [12, 0, 4, 8, 16, 20, 24, 28, 32, 40, 48, 64]
    for hl in cands:
        rem = len(seg) - hl
        if rem >= ROW_BYTES and rem % ROW_BYTES == 0:
            return hl
    return None

rows = []
debug = []

# build segments between consecutive markers
for i in range(len(hits) - 1):
    seg = raw[hits[i] : hits[i+1]]
    hl = best_header_len(seg)
    if hl is None:
        debug.append((i, len(seg), None))
        continue

    payload = seg[hl:]
    nrows = len(payload) // ROW_BYTES

    # decode as big-endian u16 (your BE stats were sane)
    u = np.frombuffer(payload, dtype=">u2") & 0x3FFF
    img_rows = u.reshape(nrows, W)

    rows.append(img_rows)
    debug.append((i, len(seg), hl, nrows))

# also use tail after last marker (may contain remaining rows of the frame)
tail = raw[hits[-1]:]
hl_tail = best_header_len(tail)
if hl_tail is not None:
    payload = tail[hl_tail:]
    nrows = len(payload) // ROW_BYTES
    u = np.frombuffer(payload[:nrows*ROW_BYTES], dtype=">u2") & 0x3FFF
    rows.append(u.reshape(nrows, W))
    debug.append(("tail", len(tail), hl_tail, nrows))
else:
    debug.append(("tail", len(tail), None))

# stack rows, then take first 192 rows
if not rows:
    print("No decodable segments (header/row alignment failed).")
    print("debug:", debug[:20])
    raise SystemExit(1)

stack = np.vstack(rows)
print("Total reconstructed rows:", stack.shape[0])
print("First 20 segment debug entries:")
for d in debug[:20]:
    print(d)

frame = stack[:H, :]  # first 192 rows
np.save("frame_rows.npy", frame)
# save PNG (no imshow on screen)
plt.figure(figsize=(5,4))
plt.imshow(frame, cmap="inferno")
plt.colorbar()
plt.title("Reassembled frame (marker blocks)")
plt.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
plt.close()

print("Saved:", OUT_PNG)

