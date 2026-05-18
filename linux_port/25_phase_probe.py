#!/usr/bin/env python3
"""Probe byte offsets for the real frame/body boundary.

The current direct-bulk slicer uses a fixed 100,436-byte period. If that period
is right but the first slice starts at the wrong byte, each frame can be
spatially shifted or flipped-looking even though the data is thermal. This
script tries many byte offsets, scores the resulting frames for smooth
thermal-like structure, and writes a montage of the best candidates.

Example:

    python linux_port/25_phase_probe.py linux_port/captures/stim_face.bin --bad 47:91

Then open the generated PNG and try the best-looking offset in the browser:

    python linux_port/24_frame_browser.py linux_port/captures/stim_face.bin --offset N --flip-y
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import numpy as np
import matplotlib.pyplot as plt

WIDTH = 256
BODY_HEIGHT = 196
BODY_BYTES = WIDTH * BODY_HEIGHT * 2
FRAME_BYTES = BODY_BYTES + 84


def parse_ranges(spec: str, n: int) -> np.ndarray:
    keep = np.ones(n, dtype=bool)
    if not spec:
        return keep
    for part in spec.split(","):
        if not part:
            continue
        if ":" in part:
            a_s, b_s = part.split(":", 1)
            a = int(a_s) if a_s else 0
            b = int(b_s) if b_s else n
            keep[max(a, 0) : min(b, n)] = False
        else:
            i = int(part)
            if 0 <= i < n:
                keep[i] = False
    return keep


def frames_at(raw: bytes, frame_bytes: int, offset: int, max_frames: int | None) -> np.ndarray:
    raw = raw[offset:]
    n = len(raw) // frame_bytes
    if max_frames is not None:
        n = min(n, max_frames)
    if n <= 2:
        raise ValueError("not enough whole frames")
    chunks = np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, frame_bytes)
    bodies = chunks[:, :BODY_BYTES].copy()
    return bodies.view(">u2").reshape(n, BODY_HEIGHT, WIDTH).astype(np.float32)


def smoothness_score(frames: np.ndarray, keep: np.ndarray, flip_y: bool) -> tuple[float, np.ndarray]:
    f = frames[keep]
    if flip_y:
        f = f[:, ::-1, :]
    # NUC-like correction: remove per-pixel temporal median, then inspect a typical frame.
    med = np.median(f, axis=0)
    corrected = f - med
    # Use the median absolute corrected image to favor offsets where stimulus forms coherent regions.
    img = np.median(np.abs(corrected), axis=0)
    # Thermal objects should be spatially smooth; misalignment/noise has high high-frequency energy.
    dx = np.abs(np.diff(img, axis=1)).mean()
    dy = np.abs(np.diff(img, axis=0)).mean()
    contrast = np.percentile(img, 99) - np.percentile(img, 50)
    hf = dx + dy + 1e-6
    score = float(contrast / hf)
    return score, img


def main() -> int:
    ap = argparse.ArgumentParser(description="Find the best byte offset for direct-bulk frame slicing")
    ap.add_argument("stream", type=Path)
    ap.add_argument("--frame-bytes", type=int, default=FRAME_BYTES)
    ap.add_argument("--scan-start", type=int, default=0)
    ap.add_argument("--scan-stop", type=int, default=512,
                    help="exclusive byte offset limit to scan (default 512)")
    ap.add_argument("--step", type=int, default=16,
                    help="byte step; use 16 for quick scans, 2 for refinement")
    ap.add_argument("--bad", default="",
                    help="frame indices/ranges to exclude, e.g. 47:91 or 47:91,123")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    raw = args.stream.read_bytes()
    n0 = (len(raw) - args.scan_start) // args.frame_bytes
    if args.max_frames is not None:
        n0 = min(n0, args.max_frames)
    keep = parse_ranges(args.bad, n0)
    print(f"stream {len(raw)} bytes; base frames={n0}; excluding {np.count_nonzero(~keep)} frames")

    results: list[tuple[float, int, bool, np.ndarray]] = []
    for offset in range(args.scan_start, min(args.scan_stop, len(raw) - BODY_BYTES), args.step):
        try:
            frames = frames_at(raw, args.frame_bytes, offset, args.max_frames)
        except ValueError:
            continue
        local_keep = keep[: frames.shape[0]]
        if np.count_nonzero(local_keep) < 3:
            continue
        for flip_y in (False, True):
            score, img = smoothness_score(frames, local_keep, flip_y)
            results.append((score, offset, flip_y, img))

    results.sort(key=lambda x: x[0], reverse=True)
    print("Top offsets:")
    for score, offset, flip_y, _ in results[: args.top]:
        print(f"  score={score:9.4f}  offset={offset:5d}  flip_y={flip_y}")

    top = results[: args.top]
    cols = 4
    rows = int(np.ceil(len(top) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 3.2 * rows), squeeze=False)
    for ax, item in zip(axes.ravel(), top):
        score, offset, flip_y, img = item
        lo, hi = np.percentile(img, [2, 98])
        ax.imshow(img, cmap="inferno", vmin=lo, vmax=max(hi, lo + 1), aspect="auto")
        ax.set_title(f"offset={offset} flip_y={flip_y}\nscore={score:.3f}")
        ax.axis("off")
    for ax in axes.ravel()[len(top):]:
        ax.axis("off")
    out = args.stream.with_suffix(".phase_probe.png")
    plt.tight_layout()
    plt.savefig(out, dpi=140)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
