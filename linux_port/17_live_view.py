#!/usr/bin/env python3
"""Live thermal view: stream frames from the camera, subtract NUC, display.

Requires a NUC reference produced by step 16 (default location:
linux_port/calibration/nuc_ref.npy). Press 'r' to re-capture a NUC reference
without restarting, 'q' to quit.
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
import usb.core
import usb.util
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

VID, PID = 0x04B4, 0x000A
EP_IN = 0x82
FRAME_BYTES = 100_436
WIRE_HEIGHT = 196
DISPLAY_HEIGHT = 192
WIDTH = 256
TRAILER_BYTES = 84
CHUNK = 16 * 1024
SYNC_DISCARD = FRAME_BYTES


def open_camera() -> usb.core.Device:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("camera 04b4:000a not found")
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        if e.errno != 16:
            raise
    try:
        dev.set_interface_altsetting(interface=0, alternate_setting=1)
    except usb.core.USBError as e:
        if e.errno == 16 and dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
            dev.set_interface_altsetting(interface=0, alternate_setting=1)
        else:
            raise
    return dev


class FrameStream:
    def __init__(self, dev: usb.core.Device, phase_offset: int):
        self.dev = dev
        self.buf = bytearray()
        self.discarded = 0
        self.sync_discard = SYNC_DISCARD + phase_offset

    def next_frame(self, timeout_ms: int = 200) -> np.ndarray | None:
        while True:
            try:
                chunk = self.dev.read(EP_IN, CHUNK, timeout=timeout_ms)
            except usb.core.USBTimeoutError:
                return None
            if self.discarded < self.sync_discard:
                take = min(len(chunk), self.sync_discard - self.discarded)
                self.discarded += take
                chunk = chunk[take:]
                if not chunk:
                    continue
            self.buf.extend(chunk)
            if len(self.buf) >= FRAME_BYTES:
                body = bytes(self.buf[: FRAME_BYTES - TRAILER_BYTES])
                del self.buf[:FRAME_BYTES]
                return np.frombuffer(body, dtype=">u2").reshape(WIRE_HEIGHT, WIDTH)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nuc", type=Path,
                    default=Path("linux_port/calibration/nuc_ref.npy"))
    ap.add_argument("--full", action="store_true",
                    help="show the full 196-row body instead of the native 192-row image region")
    ap.add_argument("--phase-offset", type=int, default=0,
                    help="extra bytes to discard after the initial settling frame; try 414")
    args = ap.parse_args()

    if not args.nuc.exists():
        print(f"NUC reference not found at {args.nuc}", file=sys.stderr)
        print("Run step 16 (16_calibrate_nuc.py) first.", file=sys.stderr)
        return 1

    nuc = np.load(args.nuc).astype(np.int32)
    print(f"loaded NUC reference {nuc.shape}")

    dev = open_camera()
    stream = FrameStream(dev, args.phase_offset)

    # Warm up: discard a few frames after sync to let internal counters settle.
    for _ in range(3):
        stream.next_frame()

    # Native ALCall is initialized with height 196 but calls ImageProcessInit(width, height - 4).
    # The Android display path therefore works on 192 image rows, using the remaining rows
    # as support/calibration data. This viewer only approximates that pipeline with NUC subtraction.
    rows = slice(0, WIRE_HEIGHT) if args.full else slice(0, DISPLAY_HEIGHT)
    height = (rows.stop or WIRE_HEIGHT) - (rows.start or 0)
    initial = np.zeros((height, WIDTH), dtype=np.float32)

    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(initial, cmap="inferno", vmin=-500, vmax=2000)
    title = ax.set_title("live thermal (NUC-subtracted)")
    plt.colorbar(im, ax=ax, label="raw − NUC")

    stats = {"fps": 0.0, "last_t": time.monotonic(), "n": 0,
             "mean_hist": [], "skipped": 0}
    last_good_frame: list[np.ndarray] = [initial.copy()]

    def update(_):
        frame = stream.next_frame()
        if frame is None:
            return im,
        nucd = frame.astype(np.int32) - nuc
        cropped = nucd[rows]

        # Outlier rejection — auto-NUC / desync frames have wildly different means.
        m = float(cropped.mean())
        stats["mean_hist"].append(m)
        if len(stats["mean_hist"]) > 30:
            stats["mean_hist"].pop(0)
        if len(stats["mean_hist"]) >= 10:
            arr = np.array(stats["mean_hist"][:-1])  # excluding the current frame
            ref_m, ref_s = arr.mean(), max(arr.std(), 1.0)
            if abs(m - ref_m) > 5 * ref_s:
                stats["skipped"] += 1
                cropped = last_good_frame[0]  # show previous good frame instead
            else:
                last_good_frame[0] = cropped
        else:
            last_good_frame[0] = cropped

        lo, hi = np.percentile(cropped, [2, 98])
        im.set_data(cropped)
        im.set_clim(lo, hi)
        stats["n"] += 1
        now = time.monotonic()
        if now - stats["last_t"] > 0.5:
            stats["fps"] = stats["n"] / (now - stats["last_t"])
            stats["n"] = 0
            stats["last_t"] = now
            title.set_text(f"live thermal — {stats['fps']:.1f} fps, "
                           f"{stats['skipped']} outliers skipped")
        return im, title

    anim = FuncAnimation(fig, update, interval=33, blit=False, cache_frame_data=False)
    plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
