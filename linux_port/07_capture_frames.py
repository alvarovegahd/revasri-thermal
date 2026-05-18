#!/usr/bin/env python3
"""Capture N raw thermal frames from the REVASRI camera (04b4:000a).

Findings that drive the choices here are in FINDINGS.md. In short:

  * The camera is a vendor-class UVC device. Interface 0 alt 1 exposes a 512-B
    bulk IN endpoint at address 0x82, which streams once we set the altsetting.
  * No firmware-update `executeCmd` call is needed to read raw bytes. Native
    disassembly showed `executeCmd` copies/flashes `artosyn-upgrade-ars31.img`;
    it is not the preview init path and should not be called for streaming.
  * Wire frame = 100 436 bytes. Measured empirically by FFT-autocorrelation of
    a 30 MB continuous capture (step 10). The peak is razor-sharp at lag
    100 436, value ~0.95 against a background of ~0.5.
  * Inside that frame: 256 wide x 196 rows x 2 B/sample = 100 352 body bytes,
    plus 84 extra bytes observed in direct bulk capture. Native ALCall then
    processes the 196-row body and displays 192 corrected rows (height - 4).

Sync: drop one full frame's worth of bytes up front so reads land on a frame
boundary, then take complete 100 436-byte chunks.
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path

import usb.core
import usb.util

VID, PID = 0x04B4, 0x000A
SENSOR_W = 256
WIRE_H = 196                                  # body rows before native ALCall correction
BYTES_PER_PIXEL = 2
TRAILER_BYTES = 84                            # extra bytes found by autocorrelation
FRAME_BYTES = SENSOR_W * WIRE_H * BYTES_PER_PIXEL + TRAILER_BYTES   # 100436
EP_IN = 0x82
CHUNK = 512                                   # bulk maxpacket on this endpoint
SYNC_DISCARD_BYTES = FRAME_BYTES              # drop one frame's worth to settle


def find_camera() -> usb.core.Device:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit(f"camera {VID:04x}:{PID:04x} not found")
    return dev


def prepare(dev: usb.core.Device) -> None:
    """Set configuration + alt setting. Both are idempotent."""
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        if e.errno != 16:  # 16 = EBUSY = already configured
            raise
    # The frame endpoints (0x82/0x06) only exist on alt 1 of interface 0.
    try:
        dev.set_interface_altsetting(interface=0, alternate_setting=1)
    except usb.core.USBError as e:
        # If the kernel already grabbed it, detach and retry once.
        if e.errno == 16 and dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
            dev.set_interface_altsetting(interface=0, alternate_setting=1)
        else:
            raise


def capture(dev: usb.core.Device, n_frames: int, timeout_ms: int, phase_offset: int) -> list[bytes]:
    """Read n_frames whole frames. Returns a list of bytes blobs.

    Sync strategy: we don't know where the host is in the camera's frame at the
    moment we start reading, so we drop one frame's worth of bytes up front.
    After that, the bulk transfers continue at the same byte cadence, so
    consecutive 100 436-byte slices are aligned to real frame boundaries.
    """
    frames: list[bytes] = []
    buf = bytearray()
    sync_discard = SYNC_DISCARD_BYTES + phase_offset
    discarded = 0
    deadline = time.monotonic() + (n_frames * 1.0 + 5.0)
    while len(frames) < n_frames:
        if time.monotonic() > deadline:
            raise TimeoutError(f"only got {len(frames)}/{n_frames} frames before wall-clock deadline")
        try:
            chunk = dev.read(EP_IN, CHUNK, timeout=timeout_ms)
        except usb.core.USBTimeoutError:
            print("  (read timeout — continuing)", file=sys.stderr)
            continue

        # Phase 1: drop the first FRAME_BYTES of stream to land on a boundary.
        if discarded < sync_discard:
            take = min(len(chunk), sync_discard - discarded)
            discarded += take
            chunk = chunk[take:]
            if discarded == sync_discard:
                print(f"  sync: discarded {sync_discard} bytes (phase offset {phase_offset})")
            if not chunk:
                continue

        buf.extend(chunk)
        while len(buf) >= FRAME_BYTES:
            frames.append(bytes(buf[:FRAME_BYTES]))
            del buf[:FRAME_BYTES]
            print(f"  frame {len(frames)}/{n_frames}")
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture raw thermal frames")
    ap.add_argument("-n", "--frames", type=int, default=10, help="frames to capture (default 10)")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("captures"), help="output directory")
    ap.add_argument("--timeout-ms", type=int, default=2000)
    ap.add_argument("--phase-offset", type=int, default=0,
                    help="extra bytes to discard after the initial settling frame; try 414")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    dev = find_camera()
    print(f"opened {dev.manufacturer!r} {dev.product!r} sn={dev.serial_number!r}")
    prepare(dev)
    print(f"capturing {args.frames} frame(s) of {FRAME_BYTES} bytes each")
    frames = capture(dev, args.frames, args.timeout_ms, args.phase_offset)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    for i, f in enumerate(frames):
        path = args.outdir / f"frame_{stamp}_{i:03d}.bin"
        path.write_bytes(f)
    print(f"saved {len(frames)} frames -> {args.outdir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
