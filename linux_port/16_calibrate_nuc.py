#!/usr/bin/env python3
"""Capture a NUC reference: the per-pixel offset to subtract from each live frame.

Procedure:
  1. Cover the lens with your hand (or aim at a uniform-temperature surface).
  2. Run this script.
  3. It captures N frames, takes the per-pixel temporal median, and writes
     `linux_port/calibration/nuc_ref.npy` (a 196x256 int32 array).

The live viewer (step 17) loads this file and subtracts it from every frame.
Re-run whenever the camera or ambient temperature shifts a lot.
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import usb.core
import usb.util

VID, PID = 0x04B4, 0x000A
EP_IN = 0x82
FRAME_BYTES = 100_436
WIRE_HEIGHT = 196
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


def capture_n_frames(dev: usb.core.Device, n: int, timeout_ms: int = 1000) -> np.ndarray:
    buf = bytearray()
    discarded = 0
    frames = np.empty((n, WIRE_HEIGHT, WIDTH), dtype=np.uint16)
    have = 0
    deadline = time.monotonic() + n * 1.0 + 5.0
    while have < n:
        if time.monotonic() > deadline:
            raise TimeoutError(f"got {have}/{n} frames before deadline")
        try:
            chunk = dev.read(EP_IN, CHUNK, timeout=timeout_ms)
        except usb.core.USBTimeoutError:
            continue
        if discarded < SYNC_DISCARD:
            take = min(len(chunk), SYNC_DISCARD - discarded)
            discarded += take
            chunk = chunk[take:]
            if not chunk:
                continue
        buf.extend(chunk)
        while len(buf) >= FRAME_BYTES and have < n:
            body = bytes(buf[: FRAME_BYTES - TRAILER_BYTES])
            frames[have] = np.frombuffer(body, dtype=">u2").reshape(WIRE_HEIGHT, WIDTH)
            del buf[:FRAME_BYTES]
            have += 1
            print(f"  frame {have}/{n}")
    return frames


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--frames", type=int, default=30)
    ap.add_argument("-o", "--output", type=Path,
                    default=Path("linux_port/calibration/nuc_ref.npy"))
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    print("Cover the lens with your hand or aim at a flat uniform surface,")
    print("then press Enter to capture the NUC reference...")
    try:
        input()
    except KeyboardInterrupt:
        return 1

    dev = open_camera()
    print(f"capturing {args.frames} frames for NUC reference")
    frames = capture_n_frames(dev, args.frames)
    nuc = np.median(frames, axis=0).astype(np.int32)
    np.save(args.output, nuc)
    print(f"saved NUC reference {nuc.shape} {nuc.dtype} -> {args.output}")
    print(f"  median {int(np.median(nuc))}  std {nuc.std():.0f}  range {int(nuc.min())}..{int(nuc.max())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
