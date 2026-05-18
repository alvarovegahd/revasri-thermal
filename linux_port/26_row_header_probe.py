#!/usr/bin/env python3
"""Test the native ALCall row-header hypothesis against direct-bulk captures.

Native `ALCall::processNUCdata` has this shape:

    if row[row_start + 6] == 0x01:
        memcpy(dst, row + 0x0c, row_stride - 0x0c)

That means the Android-side buffer may be a sequence of row records with a
12-byte header and payload after byte 12. This script checks whether the direct
USB stream already has that structure.

If the hypothesis is true, some `(row_stride, header_offset)` pair should show
byte `+6` dominated by small flag values like 0x01/0x02 across many rows. If no
pair does, then the row headers are probably created by libuvc/native repacking
before ALCall sees the data, and direct-bulk frames are lower-level raw rows.
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


def parse_int_list(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if part:
            out.append(int(part, 0))
    return out


def parse_bad(spec: str, n: int) -> np.ndarray:
    keep = np.ones(n, dtype=bool)
    if not spec:
        return keep
    for part in spec.split(","):
        if ":" in part:
            a_s, b_s = part.split(":", 1)
            a = int(a_s) if a_s else 0
            b = int(b_s) if b_s else n
            keep[max(a, 0): min(b, n)] = False
        elif part:
            i = int(part)
            if 0 <= i < n:
                keep[i] = False
    return keep


def load_chunks(path: Path, frame_bytes: int, offset: int, max_frames: int | None) -> np.ndarray:
    raw = path.read_bytes()[offset:]
    n = len(raw) // frame_bytes
    if max_frames is not None:
        n = min(n, max_frames)
    if n < 3:
        raise SystemExit("not enough whole frames for row-header analysis")
    return np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, frame_bytes)


def flag_score(chunks: np.ndarray, keep: np.ndarray, row_stride: int, rows: int, header_offset: int) -> tuple[float, dict[int, int]]:
    if header_offset + 6 >= row_stride:
        return 0.0, {}
    max_needed = header_offset + (rows - 1) * row_stride + 7
    if max_needed > chunks.shape[1]:
        rows = (chunks.shape[1] - header_offset - 7) // row_stride + 1
    if rows <= 0:
        return 0.0, {}
    pos = header_offset + np.arange(rows) * row_stride + 6
    vals = chunks[keep][:, pos].ravel()
    uniq, counts = np.unique(vals, return_counts=True)
    hist = {int(k): int(v) for k, v in zip(uniq, counts)}
    small = sum(hist.get(k, 0) for k in (0, 1, 2, 3))
    # Random raw bytes should have ~4/256 = 1.6% small values. Header flags
    # should be far higher and often dominated by one value.
    dominant = int(counts.max()) if counts.size else 0
    total = int(vals.size)
    score = (small / max(total, 1)) + (dominant / max(total, 1))
    return float(score), hist


def body_view(chunks: np.ndarray, keep: np.ndarray, offset_in_chunk: int, flip_y: bool) -> np.ndarray:
    bodies = chunks[keep, offset_in_chunk: offset_in_chunk + BODY_BYTES]
    if bodies.shape[1] != BODY_BYTES:
        raise ValueError("not enough body bytes for view")
    frames = bodies.copy().view(">u2").reshape(-1, BODY_HEIGHT, WIDTH).astype(np.float32)
    if flip_y:
        frames = frames[:, ::-1, :]
    med = np.median(frames, axis=0)
    return np.median(np.abs(frames - med), axis=0)


def payload_view(chunks: np.ndarray, keep: np.ndarray, row_stride: int, header_offset: int, payload_offset: int, payload_u16: int, rows: int, flip_y: bool) -> np.ndarray | None:
    row_bytes = payload_u16 * 2
    max_needed = header_offset + (rows - 1) * row_stride + payload_offset + row_bytes
    if max_needed > chunks.shape[1]:
        rows = (chunks.shape[1] - header_offset - payload_offset - row_bytes) // row_stride + 1
    if rows <= 2:
        return None
    out = np.empty((np.count_nonzero(keep), rows, payload_u16), dtype=np.float32)
    pos = header_offset + np.arange(rows) * row_stride + payload_offset
    for fi, chunk in enumerate(chunks[keep]):
        rows_u8 = np.stack([chunk[p: p + row_bytes] for p in pos], axis=0).copy()
        out[fi] = rows_u8.view(">u2").reshape(rows, payload_u16)
    if flip_y:
        out = out[:, ::-1, :]
    med = np.median(out, axis=0)
    return np.median(np.abs(out - med), axis=0)


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe native row-header layout")
    ap.add_argument("stream", type=Path)
    ap.add_argument("--offset", type=int, default=414,
                    help="byte offset before frame slicing (default from phase probe: 414)")
    ap.add_argument("--frame-bytes", type=int, default=FRAME_BYTES)
    ap.add_argument("--bad", default="47:91")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--strides", default="512,524,520,528,516",
                    help="comma-separated row stride candidates")
    ap.add_argument("--rows", type=int, default=196)
    ap.add_argument("--header-scan", type=int, default=64,
                    help="scan header offsets 0..N-1")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    chunks = load_chunks(args.stream, args.frame_bytes, args.offset, args.max_frames)
    keep = parse_bad(args.bad, chunks.shape[0])
    keep = keep[: chunks.shape[0]]
    print(f"chunks: {chunks.shape[0]} frames x {chunks.shape[1]} bytes, offset={args.offset}")
    print(f"excluding {np.count_nonzero(~keep)} frames; keeping {np.count_nonzero(keep)}")

    candidates = []
    for stride in parse_int_list(args.strides):
        for header_offset in range(args.header_scan):
            score, hist = flag_score(chunks, keep, stride, args.rows, header_offset)
            candidates.append((score, stride, header_offset, hist))
    candidates.sort(key=lambda x: x[0], reverse=True)

    print("\nTop row-header candidates for byte at header+6:")
    for score, stride, header_offset, hist in candidates[: args.top]:
        top_hist = sorted(hist.items(), key=lambda kv: kv[1], reverse=True)[:6]
        formatted = " ".join(f"0x{k:02x}:{v}" for k, v in top_hist)
        print(f"  score={score:7.4f} stride={stride:4d} header={header_offset:2d}  {formatted}")

    # Build visual comparison for the best few structural candidates.
    views: list[tuple[str, np.ndarray]] = []
    for flip_y in (False, True):
        views.append((f"raw body flip_y={flip_y}", body_view(chunks, keep, 0, flip_y)))
    for _, stride, header_offset, _ in candidates[:4]:
        for flip_y in (False, True):
            img = payload_view(
                chunks, keep,
                row_stride=stride,
                header_offset=header_offset,
                payload_offset=12,
                payload_u16=max((stride - header_offset - 12) // 2, 1),
                rows=args.rows,
                flip_y=flip_y,
            )
            if img is not None:
                views.append((f"stride={stride} header={header_offset} payload+12 flip_y={flip_y}", img))

    cols = 2
    rows = int(np.ceil(len(views) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(7 * cols, 3.5 * rows), squeeze=False)
    for ax, (title, img) in zip(axes.ravel(), views):
        lo, hi = np.percentile(img, [2, 98])
        ax.imshow(img, cmap="inferno", vmin=lo, vmax=max(hi, lo + 1), aspect="auto")
        ax.set_title(title)
        ax.axis("off")
    for ax in axes.ravel()[len(views):]:
        ax.axis("off")
    out = args.stream.with_suffix(".row_header_probe.png")
    plt.tight_layout()
    plt.savefig(out, dpi=140)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
