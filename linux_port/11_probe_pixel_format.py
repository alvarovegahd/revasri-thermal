#!/usr/bin/env python3
"""Brute-force the pixel format of a captured frame.

We know the wire frame is 100 436 B = 256×196 uint16 + 84 B trailer. What we
*don't* know is how the 256×196 sample grid maps to pixels:

  * endian: LE vs BE  (2 options)
  * bit mask: none, 0x3FFF (14-bit), 0x7FFF (15-bit), shift>>2  (4 options)
  * lane de-interleave: linear, 2-lane, 4-lane, 8-lane  (4 options)
  * shape: 196 rows × 256 cols, or transposed  (2 options)

That's 128 combinations. We render the first IMAGE_HEIGHT rows (skipping the
35-row metadata band at the bottom) for each, then tile them into a grid PNG so
you can visually pick the one that actually looks like the scene.

After identifying the winner, we'll bake it into step 08.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

WIDTH = 256
WIRE_HEIGHT = 196
IMAGE_HEIGHT = 160          # rows 0..160 from the stimulus map; 161..195 are metadata
TRAILER_BYTES = 84


def load_image_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) != WIDTH * WIRE_HEIGHT * 2 + TRAILER_BYTES:
        raise SystemExit(f"unexpected file size {len(raw)} (want {WIDTH*WIRE_HEIGHT*2 + TRAILER_BYTES})")
    return raw[: WIDTH * WIRE_HEIGHT * 2]


def lane_deinterleave(arr: np.ndarray, k: int) -> np.ndarray:
    """If sensor reads out k parallel lanes that interleave by k along the
    column axis, this undoes the interleave."""
    h, w = arr.shape
    if w % k:
        return arr
    out = np.empty_like(arr)
    for lane in range(k):
        out[:, lane::k] = arr[:, lane * (w // k) : (lane + 1) * (w // k)]
    return out


def variants(payload: bytes) -> dict[str, np.ndarray]:
    """Generate every (endian, mask, shape, lane) variant as a 2-D image.

    Since BE was the clear winner in v1, we focus on BE and explore SHAPE more.
    Shapes tried: (196, 256) row-major, (256, 196) row-major (= column-major
    of the wire), plus 1D reshape into other widths (128, 160, 192, 320, 384).
    """
    samples_le = np.frombuffer(payload, dtype="<u2")
    samples_be = np.frombuffer(payload, dtype=">u2")
    n = samples_be.size           # 50176

    bit_ops = {
        "raw":    lambda a: a,
        "&3FFF":  lambda a: a & 0x3FFF,
        "&7FFF":  lambda a: a & 0x7FFF,
    }
    # Candidate widths that divide n cleanly.
    cand_widths = [w for w in (128, 160, 192, 196, 224, 256, 320, 392, 448) if n % w == 0]

    out: dict[str, np.ndarray] = {}
    for endian, samples in (("BE", samples_be), ("LE", samples_le)):
        for bit_name, bit_fn in bit_ops.items():
            shaped = bit_fn(samples)
            for w in cand_widths:
                h = n // w
                arr = shaped.reshape(h, w)
                for kind, view in (
                    ("rowmajor", arr),
                    ("transposed", arr.T),
                ):
                    # Show up to 160 rows so all variants are visually comparable.
                    img = view[: min(view.shape[0], IMAGE_HEIGHT)]
                    key = f"{endian} {bit_name} {w}x{h} {kind}"
                    out[key] = img
    return out


def smoothness_score(img: np.ndarray) -> float:
    """Higher score = the image is locally smooth (neighbor pixels similar).
    Real thermal scenes are smooth; random byte permutations produce noise.

    We use the inverse of the mean absolute neighbor difference, after
    rescaling to 0..1 so different masks/endians are comparable.
    """
    a = img.astype(np.float64)
    a = (a - a.min()) / max(a.max() - a.min(), 1)
    dx = np.abs(np.diff(a, axis=1)).mean()
    dy = np.abs(np.diff(a, axis=0)).mean()
    return -(dx + dy)            # less neighbor diff = higher score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("frame", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--top", type=int, default=12, help="how many best variants to show in the grid")
    args = ap.parse_args()

    body = load_image_bytes(args.frame)
    all_variants = variants(body)
    print(f"generated {len(all_variants)} variants of shape ({IMAGE_HEIGHT}, {WIDTH})")

    ranked = sorted(all_variants.items(), key=lambda kv: smoothness_score(kv[1]), reverse=True)
    print("\nTop-ranked (by neighbor smoothness — but visual inspection wins):")
    for name, img in ranked[: args.top]:
        s = smoothness_score(img)
        print(f"  {s:+.4f}  {name}  std={img.std():.0f}  range={int(img.min())}..{int(img.max())}")

    n = min(args.top, len(ranked))
    cols = 4
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3, rows * 2.8))
    axes = np.array(axes).reshape(-1)
    for ax, (name, img) in zip(axes, ranked[:n]):
        lo, hi = np.percentile(img, [1, 99])
        ax.imshow(img, cmap="inferno", vmin=lo, vmax=max(hi, lo + 1))
        ax.set_title(name, fontsize=8)
        ax.set_xticks([])
        ax.set_yticks([])
    for ax in axes[n:]:
        ax.axis("off")
    plt.tight_layout()
    out = args.output or args.frame.with_suffix(".probe.png")
    plt.savefig(out, dpi=120)
    print(f"\nwrote {out}")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
