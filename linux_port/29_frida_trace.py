#!/usr/bin/env python3
"""
Spawn (or attach to) the REVASRI thermal-camera app under Frida, log every
USB bulk transfer + sendCmd/executeCmd to a JSONL file, and print a short
human-readable summary as events arrive.

Usage:
    python 29_frida_trace.py                  # spawn fresh
    python 29_frida_trace.py --attach         # attach to running app
    python 29_frida_trace.py --duration 60    # auto-stop after N seconds

Output: linux_port/captures/frida_trace_YYYYmmdd_HHMMSS.jsonl
"""

import argparse
import datetime as _dt
import json
import pathlib
import signal
import sys
import time

import frida

PACKAGE = "com.inreii.neutralapp"
HERE = pathlib.Path(__file__).resolve().parent
AGENT_PATH = HERE / "frida" / "trace_usb.js"
CAPTURES = HERE / "captures"


def short_hex(h: str, n: int = 32) -> str:
    if not h:
        return ""
    return h[: n * 2] + ("…" if len(h) > n * 2 else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach", action="store_true", help="attach to running app instead of spawning")
    ap.add_argument("--cold-init", action="store_true",
                    help="wait for the package to be spawned externally (e.g. by USB-attach intent) "
                         "and hook it before it runs. Disables spawn for the package on this run.")
    ap.add_argument("--duration", type=float, default=0.0, help="stop after N seconds (0 = run forever)")
    ap.add_argument("--package", default=PACKAGE)
    args = ap.parse_args()

    CAPTURES.mkdir(exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = CAPTURES / f"frida_trace_{ts}.jsonl"
    out_fp = out_path.open("w", buffering=1)  # line-buffered

    device = frida.get_usb_device(timeout=5)
    print(f"[+] device: {device.name} ({device.id})")

    # ---- cold-init mode ----------------------------------------------------
    if args.cold_init:
        # Make sure the app is fully dead so a fresh spawn is needed.
        try:
            running = device.get_process(args.package).pid
            device.kill(running)
            time.sleep(0.5)
        except frida.ProcessNotFoundError:
            pass

        # Hold every new process at spawn time; we'll resume them by hand.
        device.enable_spawn_gating()
        print("[+] spawn gating ON — waiting for an external launch of "
              f"{args.package}.  Now (re)plug the camera into the phone.")

        caught = {"pid": None, "session": None, "script": None}

        agent_src = AGENT_PATH.read_text()
        n_events = {"total": 0}

        def on_message(msg, data):
            if msg["type"] != "send":
                print(f"[!] {msg}", file=sys.stderr); return
            payload = msg["payload"]
            payload["_ts"] = time.time()
            out_fp.write(json.dumps(payload) + "\n")
            n_events["total"] += 1
            k = payload.get("kind")
            if k in ("info", "warn"):
                print(f"[{k}] {payload.get('msg')}")
            elif k == "usbfs_ctrl_out":
                print(f"  OUT USBFS_CTRL bmReq=0x{payload['bRequestType']:02x} bReq=0x{payload['bRequest']:02x} "
                      f"wValue=0x{payload['wValue']:04x} wIndex=0x{payload['wIndex']:04x} "
                      f"wLen={payload['wLength']:>4}  {short_hex(payload['hex'])}")
            elif k == "usbfs_ctrl_in":
                print(f"  IN  USBFS_CTRL bmReq=0x{payload['bRequestType']:02x} bReq=0x{payload['bRequest']:02x} "
                      f"wValue=0x{payload['wValue']:04x} wIndex=0x{payload['wIndex']:04x} "
                      f"got={payload['ret']:>4}  {short_hex(payload['hex'])}")
            elif k == "usbfs_bulk_out":
                print(f"  OUT USBFS_BULK ep=0x{payload['ep']:02x} len={payload['len']:>5}  {short_hex(payload['hex'])}")
            elif k == "usbfs_bulk_in":
                print(f"  IN  USBFS_BULK ep=0x{payload['ep']:02x} got={payload['got_len']:>5}  {short_hex(payload['hex'])}")
            elif k in ("highlevel", "uvc_call"):
                print(f"  >> {payload['sym']}  cmd_len={payload.get('cmd_len')}  {short_hex(payload.get('cmd_hex',''))}")
            elif k in ("highlevel_done", "uvc_done"):
                print(f"  << {payload['sym']}  ret={payload.get('ret')}")

        def on_spawn(spawn):
            if spawn.identifier == args.package and caught["pid"] is None:
                try:
                    session = device.attach(spawn.pid)
                    script = session.create_script(agent_src)
                    script.on("message", on_message)
                    script.load()
                    caught.update(pid=spawn.pid, session=session, script=script)
                    print(f"[+] gated, hooked, resuming: {spawn.identifier} pid={spawn.pid}")
                    device.resume(spawn.pid)
                except Exception as e:
                    print(f"[!] attach failed: {e}", file=sys.stderr)
                    try: device.resume(spawn.pid)
                    except Exception: pass
            else:
                # Release every other gated spawn so the phone stays usable.
                try: device.resume(spawn.pid)
                except Exception: pass

        device.on("spawn-added", on_spawn)

        # Also release anything already pending.
        for s in device.enumerate_pending_spawn():
            on_spawn(s)

        stop = {"flag": False}
        signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))

        # Wait for the catch + then run for `duration` seconds of trace.
        deadline = None
        try:
            while not stop["flag"]:
                if caught["pid"] is not None and deadline is None:
                    deadline = time.time() + (args.duration or 60.0)
                if deadline is not None and time.time() >= deadline:
                    break
                time.sleep(0.1)
        finally:
            try: device.disable_spawn_gating()
            except Exception: pass
            if caught["script"] is not None:
                try: caught["script"].unload()
                except Exception: pass
            out_fp.close()
            print(f"\n[+] {n_events['total']} events  →  {out_path}")
        return 0
    # ---- end cold-init mode -----------------------------------------------

    if args.attach:
        pid = None
        for app in device.enumerate_applications():
            if app.identifier == args.package and app.pid > 0:
                pid = app.pid
                break
        if pid is None:
            print(f"[!] {args.package} is not running on the device", file=sys.stderr)
            return 1
        session = device.attach(pid)
        print(f"[+] attached to {args.package} (pid {pid})")
    else:
        # Kill any prior instance so we get a clean init sequence.
        try:
            running_pid = device.get_process(args.package).pid
            device.kill(running_pid)
            print(f"[+] killed running pid {running_pid}")
            time.sleep(0.5)
        except frida.ProcessNotFoundError:
            pass
        pid = device.spawn([args.package])
        session = device.attach(pid)
        print(f"[+] spawned pid {pid}")

    script = session.create_script(AGENT_PATH.read_text())

    n_events = {"total": 0}

    def on_message(msg, data):
        if msg["type"] != "send":
            print(f"[!] {msg}", file=sys.stderr)
            return
        payload = msg["payload"]
        payload["_ts"] = time.time()
        out_fp.write(json.dumps(payload) + "\n")
        n_events["total"] += 1

        k = payload.get("kind")
        if k == "info" or k == "warn":
            print(f"[{k}] {payload.get('msg')}")
        elif k in ("bulk_out", "intr_out"):
            tag = "BULK" if k.startswith("bulk") else "INTR"
            print(f"  OUT {tag} ep=0x{payload['ep']:02x} len={payload['req_len']:>5}  {short_hex(payload['hex'])}")
        elif k in ("bulk_in", "intr_in"):
            tag = "BULK" if k.startswith("bulk") else "INTR"
            print(f"  IN  {tag} ep=0x{payload['ep']:02x} got={payload['got_len']:>5} ret={payload['ret']:>3}  {short_hex(payload['hex'])}")
        elif k == "ctrl_out":
            print(f"  OUT CTRL bmReq=0x{payload['bmReq']:02x} bReq=0x{payload['bReq']:02x} "
                  f"wValue=0x{payload['wValue']:04x} wIndex=0x{payload['wIndex']:04x} "
                  f"wLen={payload['wLen']:>4}  {short_hex(payload['hex'])}")
        elif k == "ctrl_in":
            print(f"  IN  CTRL bmReq=0x{payload['bmReq']:02x} bReq=0x{payload['bReq']:02x} "
                  f"wValue=0x{payload['wValue']:04x} wIndex=0x{payload['wIndex']:04x} "
                  f"got={payload['got_len']:>4}  {short_hex(payload['hex'])}")
        elif k == "async_submit":
            dir_ = "IN " if payload["is_in"] else "OUT"
            print(f"  ASYNC {dir_} submit ep=0x{payload['ep']:02x} type={payload['type']} "
                  f"len={payload['length']:>6}  {short_hex(payload.get('hex',''))}")
        elif k == "async_cb":
            print(f"  ASYNC cb ep=0x{payload['ep']:02x} got={payload['got_len']:>6} "
                  f"status={payload['status']}  {short_hex(payload['hex'])}")
        elif k in ("highlevel", "uvc_call"):
            print(f"  >> {payload['sym']}  cmd_len={payload.get('cmd_len')}  {short_hex(payload.get('cmd_hex',''))}")
        elif k in ("highlevel_done", "uvc_done"):
            print(f"  << {payload['sym']}  ret={payload.get('ret')}")
        elif k == "usbfs_ctrl_out":
            print(f"  OUT USBFS_CTRL bmReq=0x{payload['bRequestType']:02x} bReq=0x{payload['bRequest']:02x} "
                  f"wValue=0x{payload['wValue']:04x} wIndex=0x{payload['wIndex']:04x} "
                  f"wLen={payload['wLength']:>4}  {short_hex(payload['hex'])}")
        elif k == "usbfs_ctrl_in":
            print(f"  IN  USBFS_CTRL bmReq=0x{payload['bRequestType']:02x} bReq=0x{payload['bRequest']:02x} "
                  f"wValue=0x{payload['wValue']:04x} wIndex=0x{payload['wIndex']:04x} "
                  f"got={payload['ret']:>4}  {short_hex(payload['hex'])}")
        elif k == "usbfs_bulk_out":
            print(f"  OUT USBFS_BULK ep=0x{payload['ep']:02x} len={payload['len']:>5}  {short_hex(payload['hex'])}")
        elif k == "usbfs_bulk_in":
            print(f"  IN  USBFS_BULK ep=0x{payload['ep']:02x} got={payload['got_len']:>5}  {short_hex(payload['hex'])}")
        elif k == "usbfs_submit":
            dir_ = "IN " if payload["is_in"] else "OUT"
            print(f"  URB  {dir_} submit ep=0x{payload['ep']:02x} type={payload['type']} "
                  f"len={payload['length']:>6}  {short_hex(payload.get('hex',''))}")
        elif k == "usbfs_reap":
            print(f"  URB  reap ep=0x{payload['ep']:02x} got={payload['actual_length']:>6} "
                  f"status={payload['status']}  {short_hex(payload.get('hex',''))}")

    script.on("message", on_message)
    script.load()

    if not args.attach:
        device.resume(pid)
        print(f"[+] resumed pid {pid}")

    print(f"[+] logging to {out_path}")
    print("[+] interact with the app; Ctrl-C to stop")

    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))

    t_end = time.time() + args.duration if args.duration > 0 else float("inf")
    try:
        while not stop["flag"] and time.time() < t_end:
            time.sleep(0.2)
    finally:
        try:
            script.unload()
        except Exception:
            pass
        out_fp.close()
        print(f"\n[+] {n_events['total']} events  →  {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
