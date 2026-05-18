#!/usr/bin/env python3
"""Three diagnostics on a long stim capture, side-by-side:

  1. Magic-byte scan — look for a recurring 2- or 4-byte pattern at offsets
     that are a multiple of the known frame period (100 436). If frames start
     with a magic (à la UTi120's 0xAA55), this finds it.
  2. Per-pixel temporal median (software NUC reference) — subtract this from
     each frame to remove fixed-pattern offset. Then show one corrected frame.
  3. Frame-difference image — view (frame_t+25 - frame_t) to expose motion
     (your hand cycle was ~2 s = 50 frames at 25 fps).
"""
from __future__ import annotations
import argparse
from collections import Counter
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

FRAME_BYTES = 100_436
WIDTH = 256
WIRE_HEIGHT = 196
TRAILER_BYTES = 84    # actually unknown side; we'll test


def slice_frames(raw: bytes, offset: int = 0) -> np.ndarray:
    """Slice the stream into frames. `offset` shifts the frame boundary by N bytes."""
    raw = raw[offset:]
    n = len(raw) // FRAME_BYTES
    return np.frombuffer(raw[: n * FRAME_BYTES], dtype=np.uint8).reshape(n, FRAME_BYTES)


def magic_scan(frame_starts: np.ndarray) -> list[tuple[bytes, int]]:
    """For each frame's first 4 bytes, what's the most common value?"""
    starts = [bytes(f[:4]) for f in frame_starts]
    return Counter(starts).most_common(5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    ap.add_argument("--frame-offset", type=int, default=0,
                    help="shift frame boundary by N bytes before slicing")
    args = ap.parse_args()

    raw = args.stream.read_bytes()

    # 1) Magic-byte scan. Try the user's offset (default 0).
    frames_u8 = slice_frames(raw, args.frame_offset)
    print(f"slicing at offset {args.frame_offset}: {frames_u8.shape[0]} frames")

    print("\nTop 5 'first 4 bytes' of each frame (looking for a recurring magic):")
    for sig, count in magic_scan(frames_u8):
        print(f"  {sig.hex()}  x{count}")

    print("\nTop 5 'last 4 bytes' of each frame:")
    for sig, count in Counter(bytes(f[-4:]) for f in frames_u8).most_common(5):
        print(f"  {sig.hex()}  x{count}")

    # 2) Per-pixel temporal median — software NUC reference.
    # Drop trailer (treat last 84 bytes as non-pixel) for now; we'll later
    # also try treating first 84 as header.
    image_bytes = FRAME_BYTES - TRAILER_BYTES
    image_samples = image_bytes // 2  # uint16 count
    frames_u16 = np.empty((frames_u8.shape[0], WIRE_HEIGHT, WIDTH), dtype=np.uint16)
    for i, f in enumerate(frames_u8):
        frames_u16[i] = np.frombuffer(bytes(f[:image_bytes]), dtype=">u2").reshape(WIRE_HEIGHT, WIDTH)

    baseline = np.median(frames_u16, axis=0).astype(np.int32)
    nucd = frames_u16.astype(np.int32) - baseline

    # Pick a frame from the "warm" half (hand on lens) to visualize.
    means = frames_u16[:, :160].astype(np.float32).mean(axis=(1, 2))
    warm_idx = int(np.argmax(means))
    cold_idx = int(np.argmin(means))
    print(f"\nwarmest frame #{warm_idx} (mean {means[warm_idx]:.0f}), coldest #{cold_idx} (mean {means[cold_idx]:.0f})")

    fig, axes = plt.subplots(2, 2, figsize=(11, 9))

    im0 = axes[0, 0].imshow(frames_u16[warm_idx], cmap="inferno")
    axes[0, 0].set_title(f"raw warm frame #{warm_idx}")
    plt.colorbar(im0, ax=axes[0, 0])

    im1 = axes[0, 1].imshow(baseline, cmap="inferno")
    axes[0, 1].set_title("per-pixel temporal median (NUC ref)")
    plt.colorbar(im1, ax=axes[0, 1])

    nucd_warm = nucd[warm_idx]
    lim = np.percentile(np.abs(nucd_warm), 98)
    im2 = axes[1, 0].imshow(nucd_warm, cmap="seismic", vmin=-lim, vmax=lim)
    axes[1, 0].set_title(f"warm − baseline (software NUC) #{warm_idx}")
    plt.colorbar(im2, ax=axes[1, 0])

    # Frame-difference: 25 frames apart (~1s).
    if frames_u16.shape[0] > 26:
        d = nucd[26].astype(np.float32) - nucd[1].astype(np.float32)
        lim2 = np.percentile(np.abs(d), 98)
        im3 = axes[1, 1].imshow(d, cmap="seismic", vmin=-lim2, vmax=lim2)
        axes[1, 1].set_title("frame[26] − frame[1] (motion in 1 s)")
        plt.colorbar(im3, ax=axes[1, 1])

    out = args.stream.with_suffix(".anatomy.png")
    plt.tight_layout()
    plt.savefig(out, dpi=120)
    print(f"wrote {out}")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
