import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import gc

# ---------- CONFIG ----------
DUMP_PATH = "thermal_dump.bin"
OUTDIR = Path("brute_candidates")
OUTDIR.mkdir(exist_ok=True)

H, W = 192, 256
FRAME_BYTES_16 = H * W * 2

TOPK = 30          # save only top K candidates
MAX_TESTS = 2000   # hard cap to avoid runaway
# ---------------------------

raw = Path(DUMP_PATH).read_bytes()
raw = raw[: min(len(raw), 2_000_000)]  # cap input for speed; increase later

def save_img(img, name):
    plt.figure(figsize=(4,3))
    plt.imshow(img, cmap="inferno")
    plt.colorbar()
    plt.title(name)
    plt.savefig(OUTDIR / f"{name}.png", dpi=120)
    plt.close("all")
    gc.collect()

def smooth_score(img_u16):
    # Lower is better: neighbor absolute differences (image-like -> smoother)
    img = img_u16.astype(np.int32)
    dh = np.mean(np.abs(img[:, 1:] - img[:, :-1]))
    dv = np.mean(np.abs(img[1:, :] - img[:-1, :]))
    return float(dh + dv)

def try_make_base_frames(buf):
    """Generate a few plausible base interpretations from bytes -> uint16 frame."""
    bases = []

    # take first frame-sized chunk; if framing is off, brute later with offsets
    chunk = buf[:FRAME_BYTES_16]
    if len(chunk) < FRAME_BYTES_16:
        return bases

    b = np.frombuffer(chunk, dtype=np.uint8)

    # variants of byte/word arrangement before interpreting as u16
    # v0: big endian u16
    bases.append(("be_u16", np.frombuffer(chunk, dtype=">u2").reshape(H, W)))

    # v1: little endian u16
    bases.append(("le_u16", np.frombuffer(chunk, dtype="<u2").reshape(H, W)))

    # v2: swap bytes within each 16-bit word (equivalent to endian flip, but keep both)
    b_sw = b.reshape(-1, 2)[:, ::-1].reshape(-1)
    bases.append(("swap_bytes", b_sw.view(">u2").reshape(H, W)))

    # v3: swap 16-bit words inside each 32-bit block
    if b.size % 4 == 0:
        b32 = b.reshape(-1, 4)
        b32_sw = b32[:, [2,3,0,1]].reshape(-1)
        bases.append(("swap_u16_in_u32", b32_sw.view(">u2").reshape(H, W)))

    return bases

def col_interleave(img, k, mode):
    """
    k-way interleave undo candidates.
    mode:
      0: even/odd style: concat streams [0::k,1::k,...]
      1: inverse of above (scatter back)
      2: block shuffle (k blocks of width W/k)
      3: reverse blocks
    """
    if W % k != 0:
        return None

    if mode == 0:
        # collect columns by stride and concatenate
        parts = [img[:, i::k] for i in range(k)]
        return np.hstack(parts)

    if mode == 1:
        # inverse: assume input is concatenated streams; scatter back
        chunkw = W // k
        out = np.empty_like(img)
        for i in range(k):
            out[:, i::k] = img[:, i*chunkw:(i+1)*chunkw]
        return out

    if mode == 2:
        # swap blocks order (try a few deterministic patterns)
        blocks = img.reshape(H, k, W//k)
        # rotate blocks by 1
        blocks2 = np.roll(blocks, shift=1, axis=1)
        return blocks2.reshape(H, W)

    if mode == 3:
        blocks = img.reshape(H, k, W//k)
        blocks2 = blocks[:, ::-1, :]
        return blocks2.reshape(H, W)

    return None

def apply_masks(img):
    # for T256 raw, 14-bit payload is plausible
    return [
        ("raw", img),
        ("mask14", img & 0x3FFF),
        ("shift2", (img >> 2) & 0x3FFF),
    ]

# ----- MAIN BRUTE LOOP -----
candidates = []  # (score, name, img)

bases = try_make_base_frames(raw)
print("Base variants:", [n for n,_ in bases])

tests = 0
for base_name, base in bases:
    for masked_name, img0 in apply_masks(base):
        for k in [2,4,8,16]:
            for mode in [0,1,2,3]:
                img1 = col_interleave(img0, k, mode)
                if img1 is None:
                    continue

                # score
                sc = smooth_score(img1)
                name = f"{base_name}__{masked_name}__k{k}_m{mode}__s{int(sc)}"
                candidates.append((sc, name, img1.copy()))

                tests += 1
                if tests >= MAX_TESTS:
                    break
            if tests >= MAX_TESTS:
                break
        if tests >= MAX_TESTS:
            break
    if tests >= MAX_TESTS:
        break

# sort and save topK
candidates.sort(key=lambda x: x[0])
print("Total tests:", len(candidates))
print("Top 10:")
for sc, name, _ in candidates[:10]:
    print(sc, name)

for sc, name, img in candidates[:TOPK]:
    save_img(img, name)

print(f"Saved top {TOPK} to {OUTDIR}/")

