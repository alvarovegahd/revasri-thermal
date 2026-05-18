#!/usr/bin/env python3
"""Render one raw frame captured by 07_capture_frames.py.

This is a diagnostic viewer, not a clone of the Android app's image processor.
The native APK path feeds a 256x196 big-endian uint16 buffer into ALCall, then
ALCall outputs a corrected 256x192 image after NUC / stripe / bad-pixel work.
Here we only display selected rows from the raw 196-row body.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

WIDTH = 256
BODY_HEIGHT = 196               # native buffer body before ALCall correction
DISPLAY_HEIGHT = 192            # ALCall/ImageProcessInit uses height - 4
TRAILER_BYTES = 84              # extra bytes observed in direct bulk capture
DTYPE = np.uint16
BODY_BYTES = WIDTH * BODY_HEIGHT * 2                   # 100352
WIRE_BYTES = BODY_BYTES + TRAILER_BYTES                # 100436


def parse_rows(spec: str) -> slice:
    """Parse START:END row slice, where END is exclusive."""
    if ":" not in spec:
        raise argparse.ArgumentTypeError("row range must look like START:END")
    start_s, end_s = spec.split(":", 1)
    try:
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else BODY_HEIGHT
    except ValueError as exc:
        raise argparse.ArgumentTypeError("row range must use integers") from exc
    if start < 0 or end > BODY_HEIGHT or start >= end:
        raise argparse.ArgumentTypeError(f"row range must fit 0:{BODY_HEIGHT}")
    return slice(start, end)


def load_frame(path: Path) -> tuple[np.ndarray, bytes]:
    """Return (raw_196x256_body, extra_bytes).

    Direct bulk capture currently slices frames as 100,436 B:
      * 100,352 B body = 256 x 196 x uint16 big-endian
      * 84 B extra = still unexplained direct-capture remainder

    Android's native code processes the 196-row body and displays 192 rows.
    """
    raw = path.read_bytes()
    if len(raw) == WIRE_BYTES:
        body = np.frombuffer(raw[:BODY_BYTES], dtype=">u2").reshape(BODY_HEIGHT, WIDTH)
        return body, raw[BODY_BYTES:]
    if len(raw) == BODY_BYTES:
        body = np.frombuffer(raw, dtype=">u2").reshape(BODY_HEIGHT, WIDTH)
        return body, b""
    raise ValueError(f"{path}: {len(raw)} bytes - expected {WIRE_BYTES} (wire) or {BODY_BYTES} (body)")


def to_png(frame: np.ndarray) -> Image.Image:
    """Min/max normalize the 16-bit frame into an 8-bit grayscale PNG."""
    lo, hi = np.percentile(frame, [1, 99])
    span = max(hi - lo, 1)
    norm = np.clip((frame.astype(np.float32) - lo) / span, 0, 1)
    return Image.fromarray((norm * 255).astype(np.uint8), mode="L")


def main() -> int:
    ap = argparse.ArgumentParser(description="View a raw thermal frame")
    ap.add_argument("frame", type=Path, help="path to a .bin frame from step 07")
    ap.add_argument("--rows", type=parse_rows, default=parse_rows(f"0:{DISPLAY_HEIGHT}"),
                    help="body rows to render as START:END (default 0:192)")
    ap.add_argument("--show", action="store_true", help="open a matplotlib window")
    args = ap.parse_args()

    body, extra = load_frame(args.frame)
    image = body[args.rows]
    print(f"body {body.shape},  rendered rows {args.rows.start}:{args.rows.stop} -> {image.shape}")
    print(f"image  min {int(image.min())}  max {int(image.max())}  median {int(np.median(image))}  std {image.std():.0f}")
    if extra:
        print(f"extra ({len(extra)} B): {extra.hex()}")

    png_path = args.frame.with_suffix(".png")
    to_png(image).save(png_path)
    print(f"wrote {png_path}")

    if args.show:
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(11, 5))
        lo, hi = np.percentile(image, [2, 98])
        im = axes[0].imshow(image, cmap="inferno", vmin=lo, vmax=max(hi, lo + 1))
        axes[0].set_title(f"{args.frame.name}\nrows {args.rows.start}:{args.rows.stop}, 2-98% stretch")
        plt.colorbar(im, ax=axes[0], label="raw BE uint16")
        axes[1].imshow(body, cmap="inferno",
                       vmin=np.percentile(body, 2), vmax=np.percentile(body, 98))
        axes[1].axhline(args.rows.start - 0.5, color="cyan", linestyle="--", linewidth=0.8)
        axes[1].axhline(args.rows.stop - 0.5, color="cyan", linestyle="--", linewidth=0.8)
        axes[1].axhline(DISPLAY_HEIGHT - 0.5, color="white", linestyle=":", linewidth=0.8)
        axes[1].set_title("full 196-row body (white = native 192-row boundary)")
        plt.tight_layout()
        plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
