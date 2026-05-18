#!/usr/bin/env python3
"""Extract and visualize the clearest 'hot stimulus' frame with proper NUC.

Uses cold frames (when the stimulus was out of FOV) as the NUC reference, then
subtracts it from each hot frame. Picks the hot frame with the highest contrast
after NUC and saves a high-quality, large PNG.
"""
from __future__ import annotations
import argparse
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

FRAME_BYTES = 100_436
WIDTH = 256
WIRE_HEIGHT = 196
TRAILER_BYTES = 84


def load_frames(stream_path: Path) -> np.ndarray:
    raw = stream_path.read_bytes()
    n = len(raw) // FRAME_BYTES
    image_bytes = FRAME_BYTES - TRAILER_BYTES
    frames = np.empty((n, WIRE_HEIGHT, WIDTH), dtype=np.uint16)
    for i in range(n):
        start = i * FRAME_BYTES
        frames[i] = np.frombuffer(raw[start : start + image_bytes], dtype=">u2").reshape(WIRE_HEIGHT, WIDTH)
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", type=Path)
    ap.add_argument("--image-rows", type=int, default=160,
                    help="rows 0..N-1 are treated as image data (rest as metadata)")
    ap.add_argument("--frame-index", type=int, default=None,
                    help="show a specific frame index instead of auto-picking")
    args = ap.parse_args()

    frames = load_frames(args.stream)
    print(f"loaded {frames.shape[0]} frames of shape {frames.shape[1:]}")

    # Pick hot/cold sets via mean over image rows only.
    means = frames[:, : args.image_rows].astype(np.float32).mean(axis=(1, 2))
    threshold = np.median(means)
    hot_idx = np.where(means > threshold)[0]
    cold_idx = np.where(means <= threshold)[0]
    print(f"hot {len(hot_idx)}  cold {len(cold_idx)}  threshold {threshold:.0f}")

    # NUC reference = mean of cold frames (stimulus out of FOV).
    nuc = frames[cold_idx].astype(np.float64).mean(axis=0)

    # NUC-subtract every hot frame; rank by contrast, drop the first 10 frames
    # (warmup / sync artifacts), then pick the **median** by std rather than the
    # max — robust against outliers like the startup spike in frame 0.
    contrasts = []
    for i in hot_idx:
        if i < 10:
            continue
        d = frames[i].astype(np.float64) - nuc
        img = d[: args.image_rows]
        contrasts.append((i, img.std()))
    if args.frame_index is not None:
        best_idx, best_std = args.frame_index, 0.0
    else:
        contrasts.sort(key=lambda kv: kv[1])
        # Top quartile by contrast — these are clearly stimulus frames, not borderline.
        top = contrasts[len(contrasts) * 3 // 4 :]
        # Pick the median of the top quartile (typical strong hot frame).
        best_idx, best_std = top[len(top) // 2]
    print(f"chosen hot frame: #{best_idx}  contrast std = {best_std:.0f}")

    best = (frames[best_idx].astype(np.float64) - nuc)[: args.image_rows]

    # Aggressive contrast stretch: clip to 2..98 percentile then map to [0, 255].
    lo, hi = np.percentile(best, [2, 98])
    stretched = np.clip((best - lo) / max(hi - lo, 1), 0, 1)

    fig, axes = plt.subplots(2, 2, figsize=(11, 9))

    im0 = axes[0, 0].imshow(frames[best_idx][: args.image_rows], cmap="inferno")
    axes[0, 0].set_title(f"raw hot frame #{best_idx}")
    plt.colorbar(im0, ax=axes[0, 0])

    im1 = axes[0, 1].imshow(nuc[: args.image_rows], cmap="inferno")
    axes[0, 1].set_title(f"NUC reference (mean of {len(cold_idx)} cold frames)")
    plt.colorbar(im1, ax=axes[0, 1])

    lim = max(abs(lo), abs(hi))
    im2 = axes[1, 0].imshow(best, cmap="seismic", vmin=-lim, vmax=lim)
    axes[1, 0].set_title(f"hot − NUC reference (signed)")
    plt.colorbar(im2, ax=axes[1, 0])

    axes[1, 1].imshow(stretched, cmap="inferno")
    axes[1, 1].set_title("hot − NUC (2-98% stretch, inferno)")

    out = args.stream.with_suffix(".best_face.png")
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    print(f"wrote {out}")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
