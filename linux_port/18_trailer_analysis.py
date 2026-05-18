#!/usr/bin/env python3
"""Reverse-engineer the 84-byte per-frame trailer.

For each of the 42 uint16 fields in the trailer, plot its value over frames in
a long capture. We're looking for:
  - Monotonic counters (frame numbers)
  - Toggling flags (NUC events, shutter state)
  - Slowly drifting temperatures
  - Constants
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

FRAME_BYTES = 100_436
TRAILER_BYTES = 84
TRAILER_U16 = TRAILER_BYTES // 2  # 42


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    args = ap.parse_args()

    raw = args.stream.read_bytes()
    n = len(raw) // FRAME_BYTES
    print(f"{n} frames in {args.stream}")

    # Pull trailers out. Each frame's trailer = last 84 bytes of its 100436-byte block.
    trailers = np.empty((n, TRAILER_U16), dtype=np.uint16)
    for i in range(n):
        start = i * FRAME_BYTES + (FRAME_BYTES - TRAILER_BYTES)
        # Try both endians and keep the LE one (typically the right one for metadata).
        trailers[i] = np.frombuffer(raw[start : start + TRAILER_BYTES], dtype="<u2")
    print(f"trailer shape: {trailers.shape}")

    # Also compute frame-mean (over the image region 110..195) — to mark noisy frames.
    means = np.empty(n)
    for i in range(n):
        start = i * FRAME_BYTES
        # rows 110..195 of BE uint16, width 256
        pixel_block = raw[start + 110 * 256 * 2 : start + 196 * 256 * 2]
        pixels = np.frombuffer(pixel_block, dtype=">u2")
        means[i] = pixels.mean()

    # Classify each trailer field.
    print("\nPer-field summary (col index, range, std, monotonic?):")
    monotonic_cols = []
    flag_cols = []
    for c in range(TRAILER_U16):
        col = trailers[:, c]
        unique = np.unique(col)
        diffs = np.diff(col.astype(np.int32))
        is_monotonic = (diffs >= 0).all() or (diffs <= 0).all()
        if is_monotonic and len(unique) > 5:
            monotonic_cols.append(c)
        if len(unique) <= 4:
            flag_cols.append(c)
        print(f"  field {c:2d}: range {int(col.min()):5d}..{int(col.max()):5d}  "
              f"unique={len(unique):3d}  std={col.std():7.1f}  "
              f"{'MONOTONIC' if is_monotonic else ''}")

    print(f"\nLikely counters (monotonic, >5 distinct values): fields {monotonic_cols}")
    print(f"Likely flags (≤4 distinct values): fields {flag_cols}")

    fig, axes = plt.subplots(7, 6, figsize=(15, 14), sharex=True)
    for c in range(TRAILER_U16):
        ax = axes.flat[c]
        ax.plot(trailers[:, c], linewidth=0.6)
        ax.set_title(f"f{c}", fontsize=7)
        ax.tick_params(labelsize=6)

    out = args.stream.with_suffix(".trailer.png")
    plt.tight_layout()
    plt.savefig(out, dpi=110)
    print(f"\nwrote {out}")

    # Also: plot frame mean and overlay possible-flag values to spot correlation.
    if flag_cols:
        fig2, ax2 = plt.subplots(figsize=(11, 5))
        ax2.plot(means, label="image-region mean", color="black")
        ax2.set_xlabel("frame #")
        ax2.set_ylabel("mean", color="black")
        ax2b = ax2.twinx()
        for c in flag_cols[:4]:
            ax2b.plot(trailers[:, c], label=f"trailer f{c}", linewidth=0.6, alpha=0.7)
        ax2b.legend(loc="upper right")
        ax2.legend(loc="upper left")
        ax2.set_title("frame mean vs trailer flag-like fields")
        out2 = args.stream.with_suffix(".trailer_vs_mean.png")
        plt.tight_layout()
        plt.savefig(out2, dpi=110)
        print(f"wrote {out2}")

    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
