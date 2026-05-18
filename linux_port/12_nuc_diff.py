#!/usr/bin/env python3
"""Test whether the noisy raw frames just need NUC (non-uniformity correction).

Microbolometer sensors give each pixel a large per-pixel offset that swamps
the scene signal. Subtracting a reference frame (taken with the lens covered)
removes that offset.

This script uses a long stimulus capture (from step 09) and the per-frame mean
intensity to automatically separate "hand on lens" frames from "lens uncovered"
frames, then shows their *difference* image. If the pixel decode is correct,
the difference should look like the silhouette of the warm object (your hand).
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

FRAME_BYTES = 100_436
WIDTH = 256
WIRE_HEIGHT = 196
TRAILER_BYTES = 84


def slice_into_frames(raw: bytes) -> np.ndarray:
    n_frames = len(raw) // FRAME_BYTES
    print(f"{len(raw)} bytes -> {n_frames} frames of {FRAME_BYTES} B each")
    frames = np.empty((n_frames, WIRE_HEIGHT, WIDTH), dtype=np.uint16)
    for i in range(n_frames):
        start = i * FRAME_BYTES
        body = raw[start : start + FRAME_BYTES - TRAILER_BYTES]
        frames[i] = np.frombuffer(body, dtype=">u2").reshape(WIRE_HEIGHT, WIDTH)
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    ap.add_argument("--mask", choices=("raw", "3fff", "7fff"), default="raw")
    args = ap.parse_args()

    raw = args.stream.read_bytes()
    frames = slice_into_frames(raw)
    if args.mask == "3fff":
        frames = frames & 0x3FFF
    elif args.mask == "7fff":
        frames = frames & 0x7FFF

    # Per-frame mean intensity — proxy for "hand on lens" (warm, high) vs off (cool, low).
    # Use only the suspected-image rows (0..160) so the static metadata doesn't dominate.
    means = frames[:, :160].mean(axis=(1, 2))
    threshold = np.median(means)
    hot_idx = np.where(means > threshold)[0]
    cold_idx = np.where(means <= threshold)[0]
    print(f"hot frames: {len(hot_idx)} (mean above {threshold:.0f})")
    print(f"cold frames: {len(cold_idx)} (mean at or below)")

    if len(hot_idx) < 5 or len(cold_idx) < 5:
        print("WARNING: very imbalanced split — pattern may be wrong")

    hot_mean = frames[hot_idx].astype(np.float64).mean(axis=0)
    cold_mean = frames[cold_idx].astype(np.float64).mean(axis=0)
    diff = hot_mean - cold_mean

    # Also keep a per-pixel temporal std map (which we already know reveals
    # pixels vs metadata) to overlay.
    per_pixel_std = frames.astype(np.float64).std(axis=0)

    fig, axes = plt.subplots(2, 2, figsize=(11, 9))

    im0 = axes[0, 0].imshow(hot_mean, cmap="inferno")
    axes[0, 0].set_title(f"mean of HOT frames ({len(hot_idx)})")
    plt.colorbar(im0, ax=axes[0, 0])

    im1 = axes[0, 1].imshow(cold_mean, cmap="inferno")
    axes[0, 1].set_title(f"mean of COLD frames ({len(cold_idx)})")
    plt.colorbar(im1, ax=axes[0, 1])

    lo, hi = np.percentile(diff, [2, 98])
    im2 = axes[1, 0].imshow(diff, cmap="seismic", vmin=-max(abs(lo), abs(hi)), vmax=max(abs(lo), abs(hi)))
    axes[1, 0].set_title("HOT − COLD  (NUC-subtracted signal)")
    plt.colorbar(im2, ax=axes[1, 0])

    im3 = axes[1, 1].imshow(per_pixel_std, cmap="viridis")
    axes[1, 1].set_title("per-pixel temporal std")
    plt.colorbar(im3, ax=axes[1, 1])

    out = args.stream.with_suffix(".nuc_diff.png")
    plt.tight_layout()
    plt.savefig(out, dpi=120)
    print(f"wrote {out}")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
