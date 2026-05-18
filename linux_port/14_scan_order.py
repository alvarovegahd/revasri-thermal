#!/usr/bin/env python3
"""Find the sensor's true scan-out order by reshaping the per-pixel median map
under many (width, height, row/column-major, 2-tap deinterleave) variants.

The per-pixel temporal median of 301 frames shows fixed-pattern sensor features
that don't move with the scene. Those features include a clearly visible
diagonal stripe in the default (196, 256) row-major view — meaning a
*horizontal* line in the real scene is being laid out diagonally in memory.

This script computes the median once, then *reshapes* it into many candidate
geometries. The right interpretation is the one where the diagonal becomes a
straight horizontal/vertical line and the static rectangle becomes axis-aligned.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

FRAME_BYTES = 100_436
TRAILER_BYTES = 84


def load_median(stream_path: Path, endian: str) -> np.ndarray:
    """Compute per-pixel temporal median (1-D, length = (FRAME_BYTES - TRAILER_BYTES) / 2)."""
    raw = stream_path.read_bytes()
    n = len(raw) // FRAME_BYTES
    image_bytes = FRAME_BYTES - TRAILER_BYTES
    flat = np.empty((n, image_bytes // 2), dtype=np.uint16)
    for i in range(n):
        start = i * FRAME_BYTES
        flat[i] = np.frombuffer(raw[start : start + image_bytes], dtype=f"{endian}u2")
    return np.median(flat, axis=0)


def two_tap_deinterleave(arr: np.ndarray) -> np.ndarray:
    """If the sensor uses a 2-tap ROIC, consecutive samples in memory might
    alternate between two amplifier outputs. De-interleave: even indices →
    left half, odd indices → right half."""
    flat = arr.reshape(-1)
    h = flat.size // 2
    return np.concatenate([flat[0::2], flat[1::2]]).reshape(arr.shape)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    ap.add_argument("--endian", choices=("<", ">"), default=">", help="< = LE, > = BE")
    args = ap.parse_args()

    median = load_median(args.stream, args.endian)
    print(f"per-pixel median shape: {median.shape}")
    n = median.size

    # Candidate widths that divide cleanly.
    widths = [w for w in (96, 100, 120, 128, 160, 192, 196, 224, 256, 320, 392) if n % w == 0]
    print(f"candidate widths that divide {n} evenly: {widths}")

    # For each width, two views: row-major (h, w) and column-major / transposed.
    # Also: same with 2-tap de-interleave.
    variants: list[tuple[str, np.ndarray]] = []
    for w in widths:
        h = n // w
        rm = median.reshape(h, w)
        cm = median.reshape(w, h).T
        variants.append((f"{w}x{h} rowmajor", rm))
        variants.append((f"{w}x{h} colmajor", cm))
        # 2-tap de-interleaved version of the row-major reshape
        de = two_tap_deinterleave(median).reshape(h, w)
        variants.append((f"{w}x{h} rowmajor 2tap", de))

    # Score each by how much "horizontal-ness" there is — diagonals get
    # penalized because their projection onto x-axis stretches.
    def horizontal_score(img: np.ndarray) -> float:
        a = img.astype(np.float64)
        a = (a - a.min()) / max(a.max() - a.min(), 1)
        # Variance along x within rows (low if row content is similar) vs
        # variance along y. Bigger row-vs-col contrast = more horizontal structure.
        row_var = a.var(axis=1).mean()
        col_var = a.var(axis=0).mean()
        return col_var - row_var

    ranked = sorted(variants, key=lambda kv: horizontal_score(kv[1]), reverse=True)
    print("\nTop 12 variants by horizontal-structure score:")
    for name, img in ranked[:12]:
        s = horizontal_score(img)
        print(f"  {s:+.4f}  {name}  shape={img.shape}")

    fig, axes = plt.subplots(3, 4, figsize=(14, 9))
    for ax, (name, img) in zip(axes.flat, ranked[:12]):
        lo, hi = np.percentile(img, [2, 98])
        ax.imshow(img, cmap="inferno", vmin=lo, vmax=max(hi, lo + 1))
        ax.set_title(name, fontsize=8)
        ax.set_xticks([]); ax.set_yticks([])

    out = args.stream.with_suffix(".scanorder.png")
    plt.tight_layout()
    plt.savefig(out, dpi=120)
    print(f"\nwrote {out}")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
