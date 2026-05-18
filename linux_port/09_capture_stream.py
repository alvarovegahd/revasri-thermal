#!/usr/bin/env python3
"""Continuous raw capture: stream bytes from the camera for N seconds.

Writes a single .bin file (no framing assumptions) plus a JSON sidecar with
timestamps so step 10 can correlate the data against a known stimulus pattern
("hand on lens" / "hand off lens").

Typical use:

    python 09_capture_stream.py --seconds 12 -o captures/stim01.bin
    # While it runs, do something like:
    #   t=0..2s : lens uncovered (cold)
    #   t=2..4s : lens covered with hand (warm)
    #   t=4..6s : uncovered
    #   t=6..8s : covered
    #   t=8..12s: uncovered
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

import usb.core
import usb.util

VID, PID = 0x04B4, 0x000A
EP_IN = 0x82
CHUNK = 16 * 1024   # bigger reads = less per-call overhead; bulk lets us batch packets


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--timeout-ms", type=int, default=1000)
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = args.output.with_suffix(".json")

    dev = open_camera()
    print(f"opened {dev.product!r} sn={dev.serial_number!r}")
    print(f"capturing {args.seconds:.1f}s -> {args.output}")
    print("  cue yourself: e.g., uncover/cover lens on a fixed schedule")
    print("  (the wall-clock t=0 starts NOW)")

    t0 = time.monotonic()
    deadline = t0 + args.seconds
    total = 0
    timestamps: list[tuple[float, int]] = []   # (wall-time-since-t0, byte-offset)
    last_print = t0

    with open(args.output, "wb") as f:
        while time.monotonic() < deadline:
            try:
                chunk = dev.read(EP_IN, CHUNK, timeout=args.timeout_ms)
            except usb.core.USBTimeoutError:
                print("  (timeout)", file=sys.stderr)
                continue
            now = time.monotonic()
            f.write(chunk)
            timestamps.append((round(now - t0, 4), total))
            total += len(chunk)
            if now - last_print > 1.0:
                rate_mb = total / (now - t0) / 1024 / 1024
                print(f"  t={now-t0:5.1f}s  bytes={total:>9}  {rate_mb:5.1f} MB/s")
                last_print = now

    elapsed = time.monotonic() - t0
    rate_mb = total / elapsed / 1024 / 1024
    print(f"done: {total} bytes in {elapsed:.2f}s ({rate_mb:.1f} MB/s)")

    sidecar.write_text(json.dumps({
        "output_bin": str(args.output.name),
        "total_bytes": total,
        "elapsed_s": elapsed,
        "chunk_size": CHUNK,
        "endpoint_in": hex(EP_IN),
        "stimulus_protocol_hint": (
            "Record what you did and when (seconds since capture start), e.g.: "
            "[(0, 'lens uncovered'), (2, 'hand on lens'), (4, 'uncovered'), ...]"
        ),
        "stimulus_events": [],   # fill in manually before running step 10
        "byte_offset_log": timestamps[:50] + ["..."] if len(timestamps) > 50 else timestamps,
    }, indent=2))
    print(f"wrote sidecar {sidecar}")
    print("Edit the .json's 'stimulus_events' list before running step 10.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
