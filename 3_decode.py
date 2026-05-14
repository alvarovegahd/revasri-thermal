import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import gc

H, W = 192, 256
PIXELS = H * W
NEEDLE = bytes.fromhex("0a0a0001")

chunks_dir = Path("carved_chunks")
outdir = Path("marker_decode_candidates")
outdir.mkdir(exist_ok=True)

def save(img, name, title):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(title)
    plt.savefig(outdir / f"{name}.png", dpi=120)
    plt.close("all")
    gc.collect()

def score(img_u16):
    img = img_u16.astype(np.int32)
    dh = np.mean(np.abs(img[:,1:] - img[:,:-1]))
    dv = np.mean(np.abs(img[1:,:] - img[:-1,:]))
    return float(dh + dv)

def decode_simple(payload_bytes):
    # interpret as BE u16 + 14-bit payload, your best-so-far mode
    u = np.frombuffer(payload_bytes, dtype=">u2")
    if len(u) < PIXELS:
        return None
    img = (u[:PIXELS] & 0x3FFF).reshape(H, W)
    return img

def decode_rowstride(payload_bytes, row_pad_bytes):
    # interpret as BE u16; each row has padding bytes to skip
    row_bytes = W*2 + row_pad_bytes
    need = row_bytes * H
    if len(payload_bytes) < need:
        return None
    rows = []
    idx = 0
    for _ in range(H):
        row = payload_bytes[idx: idx + W*2]
        rows.append(row)
        idx += row_bytes
    u = np.frombuffer(b"".join(rows), dtype=">u2")
    img = (u & 0x3FFF).reshape(H, W)
    return img

# brute params (kept small but effective)
POST_MARKER_HDR = list(range(0, 257, 8))  # 0..256 step 8
ROW_PADS = [0, 2, 4, 8, 12, 16, 24, 32]   # bytes of padding per row
TOPK = 25

for chunk_path in sorted(chunks_dir.glob("chunk_*.bin"))[:7]:
    blob = chunk_path.read_bytes()
    pos = blob.find(NEEDLE)
    if pos == -1:
        print("No marker in", chunk_path)
        continue

    base = blob[pos + len(NEEDLE):]  # start right AFTER marker
    cand = []

    # try header lengths after marker
    for hl in POST_MARKER_HDR:
        payload = base[hl:]

        # decode 1: naive contiguous
        img = decode_simple(payload)
        if img is not None:
            cand.append((score(img), f"{chunk_path.stem}__hl{hl}__contig", img))

        # decode 2: try row padding hypotheses
        for pad in ROW_PADS:
            img2 = decode_rowstride(payload, pad)
            if img2 is not None:
                cand.append((score(img2), f"{chunk_path.stem}__hl{hl}__pad{pad}", img2))

    cand.sort(key=lambda x: x[0])
    print(chunk_path.stem, "candidates:", len(cand), "best score:", cand[0][0] if cand else None)

    # save topK per chunk
    for sc, name, img in cand[:TOPK]:
        save(img, f"{name}__s{int(sc)}", f"{name} sc={sc:.1f}")

print("Saved candidates to", outdir)

