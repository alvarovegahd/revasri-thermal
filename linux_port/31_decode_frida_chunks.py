#!/usr/bin/env python3
"""
Decode the per-bulk-transfer chunk headers captured by the Frida USB tracer
(``29_frida_trace.py``).

The REVASRI thermal camera streams every frame as 7 bulk-IN transfers on
endpoint ``0x82``, each one 14 348 bytes long.  Each transfer starts with a
12-byte header:

    offset  size  meaning
    ------  ----  -------
     0      4     timestamp / fine counter (LE u32, semantics not fully decoded)
     4      2     session / stream ID (constant within a stream, varies per
                  stream-start; matches the byte 4-5 we logged in two traces)
     6      1     valid-row-group flag = 0x01 (the same flag the native
                  ``ALCall::processNUCdata`` reads at row+6)
     7      1     0x00 (padding)
     8      1     start row of this 28-row group (1, 29, 57, 85, 113, 141, 169)
     9      3     0x000000

After the 12-byte header come 28 × 256 px × 2 B = 14 336 B of big-endian u16
sample data, contributing to the 256×196 wire image.  Seven chunks per frame
make 7 × 14 348 = 100 436 B, matching ``07_capture_frames.py``'s frame size.

Usage:
    python 31_decode_frida_chunks.py <jsonl>           # summary
    python 31_decode_frida_chunks.py <jsonl> --frames  # also dump raw frames

Outputs are written next to the input as ``<basename>.frames.bin`` (concatenated
100 436-byte raw frames suitable for the existing ``08_view_frame.py``).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter
from pathlib import Path

CHUNK_BYTES = 14_348
HEADER_BYTES = 12
ROW_BYTES = 256 * 2          # one row of u16 pixels
CHUNK_ROWS = (CHUNK_BYTES - HEADER_BYTES) // ROW_BYTES   # 28
FRAME_BYTES = 7 * CHUNK_BYTES                            # 100_436
EXPECTED_START_ROWS = [1, 29, 57, 85, 113, 141, 169]


def parse_header(b: bytes) -> dict:
    counter   = struct.unpack_from("<I", b, 0)[0]
    stream_id = struct.unpack_from("<H", b, 4)[0]
    flag      = b[6]
    pad7      = b[7]
    start_row = b[8]
    trailing  = b[9:12]
    return {
        "counter":  counter,
        "stream":   stream_id,
        "flag":     flag,
        "pad7":     pad7,
        "start":    start_row,
        "trailing": trailing.hex(),
    }


def iter_bulk_chunks(jsonl: Path):
    with jsonl.open() as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("kind") != "usbfs_bulk_in":
                continue
            if d.get("ep") != 0x82:
                continue
            yield d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--frames", action="store_true",
                    help="also write reconstructed frames to <basename>.frames.bin")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N frames (0 = all)")
    args = ap.parse_args()

    chunks_in   = 0
    chunks_used = 0
    junk        = 0
    start_row_counts: Counter[int] = Counter()
    flag_counts:      Counter[int] = Counter()
    stream_ids:       Counter[int] = Counter()
    got_lens:         Counter[int] = Counter()

    frames: list[bytes] = []   # each entry = 100_436 B
    current_chunks: list[bytes] = []
    current_expected_index = 0

    def flush_frame_if_complete():
        nonlocal current_chunks, current_expected_index
        if len(current_chunks) == 7:
            frames.append(b"".join(current_chunks))
        current_chunks = []
        current_expected_index = 0

    for d in iter_bulk_chunks(args.jsonl):
        chunks_in += 1
        got = d["got_len"]
        got_lens[got] += 1
        if got != CHUNK_BYTES:
            # Stale / truncated reads, mostly seen right after a cold start.
            junk += 1
            current_chunks = []
            current_expected_index = 0
            continue
        hex_str = d.get("hex", "")
        body = bytes.fromhex(hex_str)
        if len(body) < HEADER_BYTES:
            junk += 1
            continue
        hdr = parse_header(body[:HEADER_BYTES])
        flag_counts[hdr["flag"]] += 1
        start_row_counts[hdr["start"]] += 1
        stream_ids[hdr["stream"]] += 1

        # Reject anything that doesn't look like a real chunk header.
        if hdr["flag"] != 0x01 or hdr["start"] not in EXPECTED_START_ROWS:
            junk += 1
            current_chunks = []
            current_expected_index = 0
            continue

        expected = EXPECTED_START_ROWS[current_expected_index]
        if hdr["start"] != expected:
            # Re-sync: a new frame must start at row 1.
            if hdr["start"] == 1:
                current_chunks = [body]
                current_expected_index = 1
                continue
            current_chunks = []
            current_expected_index = 0
            continue

        current_chunks.append(body)
        current_expected_index += 1
        chunks_used += 1
        if current_expected_index == 7:
            # Note: hex captured by Frida is truncated (first 256 B per transfer).
            # That's fine for header inspection but means frame bytes here are
            # only partial.  --frames output is therefore header-accurate but
            # NOT a valid raw frame; use 07_capture_frames.py for that on Linux.
            flush_frame_if_complete()
            if args.limit and len(frames) >= args.limit:
                break

    print(f"input:           {args.jsonl}")
    print(f"bulk_in chunks:  {chunks_in}")
    print(f"used (full 14348B + valid header): {chunks_used}")
    print(f"discarded (truncated/anomalous):   {junk}")
    print(f"frames reassembled:                {len(frames)}")
    print()
    print("got_len histogram:")
    for k, v in sorted(got_lens.items(), key=lambda x: -x[1])[:8]:
        print(f"  {v:>6}  {k} B")
    print()
    print("byte 6 (valid-row-group flag) histogram:")
    for k, v in sorted(flag_counts.items(), key=lambda x: -x[1])[:6]:
        print(f"  {v:>6}  0x{k:02x}")
    print()
    print("byte 8 (start_row) histogram (expecting {1,29,57,85,113,141,169}):")
    for k, v in sorted(start_row_counts.items()):
        marker = "  <- expected" if k in EXPECTED_START_ROWS else "  <- UNEXPECTED"
        print(f"  {v:>6}  row {k:>3}{marker}")
    print()
    print("stream IDs (bytes 4-5) seen:")
    for k, v in sorted(stream_ids.items(), key=lambda x: -x[1])[:6]:
        print(f"  {v:>6}  0x{k:04x}")

    if args.frames:
        out = args.jsonl.with_suffix(".frames.bin")
        # Strip the 12-B header from each chunk before concatenating so the
        # output mirrors what would land in a libuvc frame buffer.
        bodies = []
        for f in frames:
            for i in range(7):
                start = i * CHUNK_BYTES + HEADER_BYTES
                end   = start + (CHUNK_BYTES - HEADER_BYTES)
                bodies.append(f[start:end])
        # NOTE: hex truncation in JSONL means most of each chunk is zeros.
        # This file is only useful for verifying the chunk-stitching logic
        # against real captures from 07_capture_frames.py, not for decoding.
        out.write_bytes(b"".join(bodies))
        print()
        print(f"wrote {out}  ({out.stat().st_size} B, {len(frames)} frames "
              f"of {(CHUNK_BYTES-HEADER_BYTES)*7} B each)")
        print("  (Reminder: Frida-side hex is truncated to 256 B per transfer; "
              "use 07_capture_frames.py for full pixel data.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
