#!/usr/bin/env python3
"""Interactive browser/annotator for raw continuous captures.

Use this when the automated heuristics are not trustworthy yet. It lets you
scrub through a stream captured by 09_capture_stream.py, view frames as raw,
median-NUC-subtracted, or frame-difference images, and label frames as good or
noise. Labels are written to a CSV next to the stream.

Keys:
  left/right or p/n  previous/next frame
  1                 raw view
  2                 median-NUC view
  3                 difference from previous frame
  g                 mark current frame good
  b                 mark current frame noise/bad
  u                 clear label
  s                 save current view PNG
  q                 quit
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider

WIDTH = 256
BODY_HEIGHT = 196
DISPLAY_HEIGHT = 192
TRAILER_BYTES = 84
FRAME_BYTES = WIDTH * BODY_HEIGHT * 2 + TRAILER_BYTES
BODY_BYTES = WIDTH * BODY_HEIGHT * 2


def parse_rows(spec: str) -> slice:
    if ":" not in spec:
        raise argparse.ArgumentTypeError("row range must look like START:END")
    start_s, end_s = spec.split(":", 1)
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else DISPLAY_HEIGHT
    if start < 0 or end > BODY_HEIGHT or start >= end:
        raise argparse.ArgumentTypeError(f"rows must fit 0:{BODY_HEIGHT}")
    return slice(start, end)


def load_frames(path: Path, frame_bytes: int, offset: int, max_frames: int | None) -> np.ndarray:
    raw = path.read_bytes()
    if offset < 0 or offset >= len(raw):
        raise SystemExit(f"offset {offset} is outside {path}")
    raw = raw[offset:]
    if frame_bytes < BODY_BYTES:
        raise SystemExit(f"frame size {frame_bytes} is smaller than {BODY_BYTES}-byte body")
    if len(raw) == frame_bytes:
        n = 1
    else:
        n = len(raw) // frame_bytes
    if n == 0:
        raise SystemExit(f"{path}: not enough data for one {frame_bytes}-byte frame after offset {offset}")
    if len(raw) % frame_bytes:
        print(f"warning: ignoring {len(raw) % frame_bytes} trailing bytes")
    if max_frames is not None:
        n = min(n, max_frames)
    arr = np.frombuffer(raw[: n * frame_bytes], dtype=np.uint8).reshape(n, frame_bytes)
    bodies = arr[:, :BODY_BYTES].copy()
    return bodies.view(">u2").reshape(n, BODY_HEIGHT, WIDTH)


def load_labels(path: Path) -> dict[int, str]:
    labels: dict[int, str] = {}
    if not path.exists():
        return labels
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            labels[int(row["frame"])] = row["label"]
    return labels


def save_labels(path: Path, labels: dict[int, str]) -> None:
    with path.open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=("frame", "label"))
        wr.writeheader()
        for idx in sorted(labels):
            wr.writerow({"frame": idx, "label": labels[idx]})


def stretch(a: np.ndarray) -> tuple[float, float]:
    lo, hi = np.percentile(a, [2, 98])
    if hi <= lo:
        hi = lo + 1
    return float(lo), float(hi)


def main() -> int:
    ap = argparse.ArgumentParser(description="Browse and label raw thermal frames")
    ap.add_argument("stream", type=Path, help="stream .bin from step 09 or one frame from step 07")
    ap.add_argument("--rows", type=parse_rows, default=parse_rows(f"0:{DISPLAY_HEIGHT}"),
                    help="rows to display as START:END (default 0:192)")
    ap.add_argument("--labels", type=Path, default=None,
                    help="CSV label file (default: STREAM.labels.csv)")
    ap.add_argument("--frame-bytes", type=int, default=FRAME_BYTES,
                    help=f"bytes per frame chunk (default {FRAME_BYTES})")
    ap.add_argument("--offset", type=int, default=0,
                    help="byte offset before slicing frame chunks")
    ap.add_argument("--flip-y", action="store_true",
                    help="flip displayed frames vertically")
    ap.add_argument("--max-frames", type=int, default=None)
    args = ap.parse_args()

    frames = load_frames(args.stream, args.frame_bytes, args.offset, args.max_frames)
    if args.flip_y:
        frames = frames[:, ::-1, :]
    labels_path = args.labels or args.stream.with_suffix(".labels.csv")
    labels = load_labels(labels_path)
    nuc = np.median(frames.astype(np.float32), axis=0)

    state = {"idx": 0, "mode": "nuc"}

    def view(idx: int) -> np.ndarray:
        frame = frames[idx].astype(np.float32)
        cropped = frame[args.rows]
        if state["mode"] == "raw":
            return cropped
        if state["mode"] == "diff":
            prev = frames[max(idx - 1, 0)].astype(np.float32)[args.rows]
            return cropped - prev
        return cropped - nuc[args.rows]

    fig, ax = plt.subplots(figsize=(8, 5))
    plt.subplots_adjust(bottom=0.18)
    data = view(0)
    lo, hi = stretch(data)
    im = ax.imshow(data, cmap="inferno", vmin=lo, vmax=hi, aspect="auto")
    cbar = plt.colorbar(im, ax=ax, label="raw / corrected units")
    cbar.ax.set_ylabel("raw / corrected units")

    slider_ax = fig.add_axes([0.15, 0.06, 0.70, 0.03])
    slider = Slider(slider_ax, "frame", 0, len(frames) - 1, valinit=0, valstep=1)

    def redraw() -> None:
        idx = state["idx"]
        data = view(idx)
        lo, hi = stretch(data)
        im.set_data(data)
        im.set_clim(lo, hi)
        label = labels.get(idx, "-")
        ax.set_title(
            f"{args.stream.name}  frame {idx}/{len(frames)-1}  "
            f"mode={state['mode']}  rows={args.rows.start}:{args.rows.stop}  "
            f"offset={args.offset}  flip_y={args.flip_y}  label={label}"
        )
        fig.canvas.draw_idle()

    def set_idx(idx: int) -> None:
        idx = max(0, min(len(frames) - 1, int(idx)))
        state["idx"] = idx
        if int(slider.val) != idx:
            slider.set_val(idx)
        redraw()

    def on_slider(val: float) -> None:
        state["idx"] = int(val)
        redraw()

    def on_key(event) -> None:
        idx = state["idx"]
        if event.key in ("right", "n"):
            set_idx(idx + 1)
        elif event.key in ("left", "p"):
            set_idx(idx - 1)
        elif event.key == "1":
            state["mode"] = "raw"
            redraw()
        elif event.key == "2":
            state["mode"] = "nuc"
            redraw()
        elif event.key == "3":
            state["mode"] = "diff"
            redraw()
        elif event.key == "g":
            labels[idx] = "good"
            save_labels(labels_path, labels)
            redraw()
            print(f"frame {idx}: good -> {labels_path}")
        elif event.key == "b":
            labels[idx] = "noise"
            save_labels(labels_path, labels)
            redraw()
            print(f"frame {idx}: noise -> {labels_path}")
        elif event.key == "u":
            labels.pop(idx, None)
            save_labels(labels_path, labels)
            redraw()
            print(f"frame {idx}: label cleared -> {labels_path}")
        elif event.key == "s":
            out = args.stream.with_name(f"{args.stream.stem}_frame_{idx:04d}_{state['mode']}.png")
            fig.savefig(out, dpi=160)
            print(f"saved {out}")
        elif event.key == "q":
            plt.close(fig)

    slider.on_changed(on_slider)
    fig.canvas.mpl_connect("key_press_event", on_key)
    redraw()
    print(f"loaded {len(frames)} frames; frame_bytes={args.frame_bytes} offset={args.offset}; labels: {labels_path}")
    print("keys: left/right p/n move, 1 raw, 2 nuc, 3 diff, g good, b noise, u clear, s save, q quit")
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
