#!/usr/bin/env python3
"""Live thermal view using the 14 348-byte wire-chunk format we recovered
with the Frida USB tracer (see linux_port/FINDINGS.md -> "Wire-Level Chunk
Format" and 31_decode_frida_chunks.py).

Improvements over 17_live_view.py:
  - Reads in chunk-aligned units. Each bulk-IN is exactly 14 348 B and starts
    with a 12-byte header.  We validate byte 6 == 0x01, byte 8 in the expected
    start-row set, and bytes 9-11 == 0.  That gives us a real sync condition
    (no byte-scanning, no settle-discard heuristic).
  - Uses the start-row in each header to place the 28-row group at the right
    place in the frame buffer, so we tolerate dropped chunks without smearing.
  - No host-side init.  The camera self-initializes on USB power; we just
    set_configuration + alt setting 1 and start reading.
  - Optional NUC subtraction (re-uses linux_port/calibration/nuc_ref.npy from
    16_calibrate_nuc.py).  Without it, we just auto-percentile the raw u16s.

Controls (in the matplotlib window):
  q  quit
  r  recapture NUC reference from the next 32 frames
  s  save the current frame to linux_port/captures/live_<ts>.npy
"""
from __future__ import annotations
import argparse
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import usb.core
import usb.util

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# ---- wire format -----------------------------------------------------------
VID, PID         = 0x04B4, 0x000A
EP_IN            = 0x82
EP_OUT_CMD       = 0x06                   # the "shutter / FFC" command channel
HEARTBEAT_CMD    = bytes((0x80, 0x00))    # observed in Frida traces of the APK
HEARTBEAT_PERIOD = 2.0                    # seconds between heartbeats
CHUNK_BYTES      = 14_348
HEADER_BYTES     = 12
WIDTH            = 256
CHUNK_ROWS       = (CHUNK_BYTES - HEADER_BYTES) // (WIDTH * 2)  # 28
WIRE_HEIGHT      = 196
DISPLAY_HEIGHT   = 192
EXPECTED_STARTS  = (1, 29, 57, 85, 113, 141, 169)
STARTS_SET       = set(EXPECTED_STARTS)
LAST_START       = EXPECTED_STARTS[-1]   # 169
READ_SIZE        = 8 * CHUNK_BYTES        # batch reads for throughput


# ---- USB -------------------------------------------------------------------
def open_camera() -> usb.core.Device:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit(f"camera {VID:04x}:{PID:04x} not found - plugged into the laptop?")
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        if e.errno != 16:  # 16 = EBUSY = already configured
            raise
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    try:
        dev.set_interface_altsetting(interface=0, alternate_setting=1)
    except usb.core.USBError as e:
        if e.errno != 16:
            raise
    return dev


def start_heartbeat(dev: usb.core.Device, period: float = HEARTBEAT_PERIOD) -> threading.Event:
    """Spawn a daemon thread that writes the 0x80 0x00 command to EP 0x06
    every `period` seconds. The Android APK does this; on the camera side it
    appears to drive the FFC/shutter and may also keep the stream from
    throttling.  Returns an Event you can .set() to stop the thread."""
    stop = threading.Event()

    def _loop():
        while not stop.wait(period):
            try:
                dev.write(EP_OUT_CMD, HEARTBEAT_CMD, timeout=200)
            except usb.core.USBError as e:
                # Don't crash the viewer on a transient USB hiccup; just log.
                print(f"[heartbeat] write failed: {e}", file=sys.stderr)
        # final attempt to leave the camera in a sane state
        try:
            dev.write(EP_OUT_CMD, HEARTBEAT_CMD, timeout=200)
        except Exception:
            pass

    t = threading.Thread(target=_loop, name="ffc-heartbeat", daemon=True)
    t.start()
    return stop


# ---- frame assembler -------------------------------------------------------
class FrameStream:
    """Chunk-aware reassembler.

    Buffers raw bulk-IN bytes, looks for valid 12-byte chunk headers, and
    places each 28-row group at frame[start_row-1 : start_row-1+28]. Emits
    one np.uint16[196, 256] frame every time we see the start=169 chunk
    after a clean start=1.
    """

    def __init__(self, dev: usb.core.Device):
        self.dev   = dev
        self.buf   = bytearray()
        self.frame = np.zeros((WIRE_HEIGHT, WIDTH), dtype=np.uint16)
        self.synced       = False
        self.bad_bytes    = 0
        self.good_chunks  = 0
        self.frame_starts = 0
        self.frames_out   = 0

    def _refill(self, want: int, timeout_ms: int = 500) -> bool:
        while len(self.buf) < want:
            try:
                got = self.dev.read(EP_IN, READ_SIZE, timeout=timeout_ms)
            except usb.core.USBTimeoutError:
                return False
            if got:
                self.buf.extend(got)
        return True

    def _header_ok(self, off: int) -> bool:
        b = self.buf
        return (b[off + 6] == 0x01
                and b[off + 7] == 0x00
                and b[off + 8] in STARTS_SET
                and b[off + 9]  == 0
                and b[off + 10] == 0
                and b[off + 11] == 0)

    def next_frame(self) -> np.ndarray | None:
        while True:
            if not self._refill(CHUNK_BYTES):
                return None
            if not self._header_ok(0):
                # slide one byte and re-scan
                del self.buf[:1]
                self.bad_bytes += 1
                if self.synced:
                    self.synced = False
                continue
            start = self.buf[8]
            body  = bytes(self.buf[HEADER_BYTES:CHUNK_BYTES])
            del self.buf[:CHUNK_BYTES]
            rows = np.frombuffer(body, dtype=">u2").reshape(CHUNK_ROWS, WIDTH)
            self.good_chunks += 1

            if start == 1:
                self.frame_starts += 1
                self.synced = True

            if not self.synced:
                # Saw a mid-frame chunk before we've seen any start=1: skip
                # until we resync at row 1.
                continue

            row_idx = start - 1
            self.frame[row_idx : row_idx + CHUNK_ROWS] = rows

            if start == LAST_START:
                self.frames_out += 1
                return self.frame.copy()


# ---- main ------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nuc", type=Path,
                    default=Path("linux_port/calibration/nuc_ref.npy"))
    ap.add_argument("--no-nuc", action="store_true",
                    help="show raw frames without NUC subtraction")
    ap.add_argument("--full", action="store_true",
                    help="show all 196 wire rows instead of the 192 display rows")
    ap.add_argument("--cmap", default="inferno")
    ap.add_argument("--no-flip", action="store_true",
                    help="don't vertically flip the image (default: flipped)")
    ap.add_argument("--no-heartbeat", action="store_true",
                    help="don't send the 0x80 0x00 FFC/shutter heartbeat")
    ap.add_argument("--heartbeat-period", type=float, default=HEARTBEAT_PERIOD,
                    help="seconds between heartbeat writes (default 2.0)")
    args = ap.parse_args()

    use_nuc = not args.no_nuc and args.nuc.exists()
    nuc = None
    if use_nuc:
        nuc = np.load(args.nuc).astype(np.int32)
        print(f"loaded NUC reference: {nuc.shape} from {args.nuc}")
    elif not args.no_nuc:
        print(f"warn: NUC reference not at {args.nuc}; running without it")

    dev = open_camera()

    heartbeat_stop = None
    if not args.no_heartbeat:
        heartbeat_stop = start_heartbeat(dev, args.heartbeat_period)
        print(f"heartbeat: writing {HEARTBEAT_CMD.hex()} to EP 0x{EP_OUT_CMD:02x} "
              f"every {args.heartbeat_period}s")

    stream = FrameStream(dev)

    print("waiting for first synced frame...")
    first = stream.next_frame()
    if first is None:
        print("no data received in time", file=sys.stderr)
        return 1
    print(f"synced (after {stream.bad_bytes} junk bytes, "
          f"{stream.frame_starts} frame-starts seen).")

    rows = slice(0, WIRE_HEIGHT) if args.full else slice(0, DISPLAY_HEIGHT)
    h = (rows.stop or WIRE_HEIGHT) - (rows.start or 0)

    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(np.zeros((h, WIDTH), dtype=np.float32),
                   cmap=args.cmap)
    cb = plt.colorbar(im, ax=ax)
    cb.set_label("raw − NUC" if use_nuc else "raw u16")
    title = ax.set_title("live thermal")

    stats = dict(t0=time.monotonic(), n=0, fps=0.0)
    state = dict(last_frame=first.copy(),
                 recap_count=0, recap_sum=None,
                 use_nuc=use_nuc, nuc=nuc)

    def render(frame: np.ndarray):
        state["last_frame"] = frame
        # background NUC recapture
        if state["recap_count"] > 0:
            if state["recap_sum"] is None:
                state["recap_sum"] = frame.astype(np.int64).copy()
                state["_recap_target"] = state["recap_count"]
                state["recap_count"] -= 1
            else:
                state["recap_sum"] += frame.astype(np.int64)
                state["recap_count"] -= 1
            if state["recap_count"] == 0:
                avg = (state["recap_sum"] / state["_recap_target"]).astype(np.int32)
                state["nuc"]     = avg
                state["use_nuc"] = True
                state["recap_sum"] = None
                cb.set_label("raw − NUC")
                args.nuc.parent.mkdir(parents=True, exist_ok=True)
                np.save(args.nuc, avg)
                print(f"recaptured NUC, saved to {args.nuc}")

        if state["use_nuc"]:
            view = frame[rows].astype(np.int32) - state["nuc"][rows]
        else:
            view = frame[rows].astype(np.float32)
        if not args.no_flip:
            view = view[::-1]
        lo, hi = np.percentile(view, [2, 98])
        if hi - lo < 1: hi = lo + 1
        im.set_data(view)
        im.set_clim(lo, hi)

    def update(_):
        f = stream.next_frame()
        if f is None:
            return im, title
        render(f)
        stats["n"] += 1
        now = time.monotonic()
        elapsed = now - stats["t0"]
        if elapsed > 0.5:
            stats["fps"] = stats["n"] / elapsed
            stats["n"]  = 0
            stats["t0"] = now
            title.set_text(f"live thermal — {stats['fps']:5.1f} fps   "
                           f"frames={stream.frames_out}  "
                           f"bad={stream.bad_bytes}B")
        return im, title

    def on_key(event):
        if event.key == "q":
            plt.close(fig)
        elif event.key == "r":
            print("recapturing NUC over the next 32 frames...")
            state["recap_count"] = 32
            state["recap_sum"]   = None
        elif event.key == "s":
            outdir = Path("linux_port/captures")
            outdir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            out = outdir / f"live_{ts}.npy"
            np.save(out, state["last_frame"])
            print(f"saved current frame to {out}")

    fig.canvas.mpl_connect("key_press_event", on_key)
    anim = FuncAnimation(fig, update, interval=20, blit=False,
                         cache_frame_data=False)
    plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
