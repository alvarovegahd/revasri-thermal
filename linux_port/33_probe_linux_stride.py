#!/usr/bin/env python3
"""Diagnostic: read raw bulk-IN bytes from the camera on Linux for a few
seconds, then locate every chunk header (byte 6 == 0x01, byte 8 in the
expected start-row set, bytes 9-11 == 0x00) and report the stride.

If everything is well, we expect headers exactly 14 348 B apart, and the
start_row values to cycle 1, 29, 57, 85, 113, 141, 169."""
from __future__ import annotations
import time
import sys

import numpy as np
import usb.core

VID, PID    = 0x04B4, 0x000A
EP_IN       = 0x82
READ_SIZE   = 64 * 1024
STARTS_SET  = {1, 29, 57, 85, 113, 141, 169}
HEARTBEAT_NONE = True  # don't write to EP 6 during the probe


def open_camera():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("camera 04b4:000a not found")
    try: dev.set_configuration()
    except usb.core.USBError as e:
        if e.errno != 16: raise
    try: dev.set_interface_altsetting(interface=0, alternate_setting=1)
    except usb.core.USBError as e:
        if e.errno != 16: raise
    return dev


def is_header(b: bytes, off: int) -> bool:
    if off + 12 > len(b):
        return False
    return (b[off + 6] == 0x01
            and b[off + 7] == 0x00
            and b[off + 8] in STARTS_SET
            and b[off + 9]  == 0
            and b[off + 10] == 0
            and b[off + 11] == 0)


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
    dev = open_camera()
    print(f"reading for {seconds}s ...")
    buf = bytearray()
    deadline = time.monotonic() + seconds
    n_reads = 0
    read_size_hist: dict[int, int] = {}
    while time.monotonic() < deadline:
        try:
            got = dev.read(EP_IN, READ_SIZE, timeout=500)
        except usb.core.USBTimeoutError:
            print("  timeout"); continue
        n_reads += 1
        read_size_hist[len(got)] = read_size_hist.get(len(got), 0) + 1
        buf.extend(got)

    print(f"\n{len(buf):,} bytes from {n_reads} reads")
    print("read-size histogram (top 8):")
    for sz, n in sorted(read_size_hist.items(), key=lambda x: -x[1])[:8]:
        print(f"  {n:>6} reads of {sz:>6} bytes")

    # Locate ALL headers by brute-force scan.
    print("\nscanning for valid headers...")
    offsets: list[int] = []
    starts:  list[int] = []
    bb = bytes(buf)
    i = 0
    while i + 12 <= len(bb):
        if is_header(bb, i):
            offsets.append(i)
            starts.append(bb[i + 8])
            i += 1   # don't skip — we want every match
        else:
            i += 1

    print(f"  found {len(offsets):,} valid-looking headers")
    if len(offsets) < 4:
        print("  (too few to compute stride)")
        return 1

    # Stride histogram between consecutive headers.
    diffs = np.diff(np.asarray(offsets))
    uniq, cnts = np.unique(diffs, return_counts=True)
    order = np.argsort(-cnts)
    print("\nstride histogram (gap between consecutive header offsets, top 8):")
    for k in order[:8]:
        print(f"  {cnts[k]:>6}  +{uniq[k]:>6} B")

    # Look at start_row sequence at the dominant stride.
    dominant = int(uniq[order[0]])
    print(f"\nstart_row pattern at dominant stride={dominant}:")
    seq = []
    last_off = offsets[0]
    seq.append(starts[0])
    for off, st in zip(offsets[1:], starts[1:]):
        if off - last_off == dominant:
            seq.append(st)
            last_off = off
        else:
            break
    print(f"  first {min(len(seq),28)} start_rows: {seq[:28]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
