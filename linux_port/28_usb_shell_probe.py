#!/usr/bin/env python3
"""Experimental USB command-channel probe for the embedded device.

Default mode is dry-run: it prints the packet that would be sent and exits.

The packet format is reconstructed from native `sendCmd` in libUVCCamera_dr.so.
The APK uses this channel for ROI commands and firmware update commands. Because
the same channel can run dangerous commands (`reboot`, firmware copy), this
script has a read-only whitelist and requires `--send` before it touches USB.

Safe first probe, after checking the dry-run:

    python linux_port/28_usb_shell_probe.py "echo REVASRI_PROBE"
    python linux_port/28_usb_shell_probe.py --send "echo REVASRI_PROBE"
"""
from __future__ import annotations

import argparse
import re
import struct
import sys

import usb.core
import usb.util

VID, PID = 0x04B4, 0x000A
CMD_IFACE = 2
EP_CMD_OUT = 0x02
EP_CMD_IN = 0x84
PACKET_LEN = 0x134
CMD_MAX = 0x100

DANGEROUS = re.compile(r"(;|&&|\|\||\||>|<|`|\$\(|\n|\r)")
DENY_WORDS = {
    "reboot", "poweroff", "halt", "shutdown", "rm", "rmdir", "mv", "cp",
    "touch", "sync", "dd", "mkfs", "mount", "umount", "flash_erase",
    "mtd", "fw_setenv", "busybox",
}
ALLOW_PREFIXES = (
    "echo ",
    "uname",
    "id",
    "pwd",
    "cat /proc/version",
    "cat /proc/cpuinfo",
    "cat /proc/meminfo",
)


def is_safe_readonly(cmd: str) -> tuple[bool, str]:
    stripped = cmd.strip()
    if not stripped:
        return False, "empty command"
    if len(stripped.encode("utf-8")) >= CMD_MAX:
        return False, f"command is too long; max payload is {CMD_MAX - 1} bytes"
    if DANGEROUS.search(stripped):
        return False, "contains shell metacharacters/separators"
    first = stripped.split()[0]
    if first in DENY_WORDS:
        return False, f"blocked command word: {first}"
    if not stripped.startswith(ALLOW_PREFIXES):
        return False, "not in read-only allowlist"
    return True, "ok"


def build_packet(cmd: str) -> bytes:
    """Build the 0x134-byte `ucmd` packet from native sendCmd."""
    packet = bytearray(PACKET_LEN)

    # Header values from FUN_001464c0 / sendCmd.
    struct.pack_into("<Q", packet, 0x00, 0x4F545241AAFF55FF)
    struct.pack_into("<I", packet, 0x08, 0x24)
    struct.pack_into("<Q", packet, 0x0C, 0x0000011000000110)
    struct.pack_into("<Q", packet, 0x14, 0x0)  # DAT_00125a30 is .bss, zero-init in static file.
    packet[0x1C] = 0x01
    struct.pack_into("<Q", packet, 0x24, 0x00000104000C0003)

    # Payload begins at offset 0x30: 4 bytes "ucmd" + 256-byte NUL-padded cmd.
    packet[0x30:0x34] = b"ucmd"
    raw = cmd.encode("utf-8")
    packet[0x34:0x34 + len(raw)] = raw
    return bytes(packet)


def find_device() -> usb.core.Device:
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("camera 04b4:000a not found")
    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        if e.errno != 16:
            raise
    return dev


def send_command(cmd: str, timeout_ms: int) -> bytes:
    dev = find_device()
    try:
        usb.util.claim_interface(dev, CMD_IFACE)
    except usb.core.USBError as e:
        raise SystemExit(f"could not claim command interface {CMD_IFACE}: {e}") from e

    packet = build_packet(cmd)
    try:
        written = dev.write(EP_CMD_OUT, packet, timeout=timeout_ms)
        print(f"wrote {written} bytes to EP 0x{EP_CMD_OUT:02x}")
        try:
            data = dev.read(EP_CMD_IN, 0x400, timeout=timeout_ms)
            return bytes(data)
        except usb.core.USBTimeoutError:
            print("read timed out; command may not return data on this path", file=sys.stderr)
            return b""
    finally:
        try:
            usb.util.release_interface(dev, CMD_IFACE)
        except Exception:
            pass


def printable(data: bytes) -> str:
    if not data:
        return ""
    text = "".join(chr(b) if 32 <= b < 127 or b in (9, 10, 13) else "." for b in data)
    return text.strip("\x00")


def main() -> int:
    ap = argparse.ArgumentParser(description="Dry-run or send a read-only USB shell command")
    ap.add_argument("command", help="read-only command, e.g. 'echo REVASRI_PROBE'")
    ap.add_argument("--send", action="store_true",
                    help="actually send to the camera; default is dry-run only")
    ap.add_argument("--allow-any", action="store_true",
                    help="disable read-only allowlist; still blocks shell metacharacters")
    ap.add_argument("--timeout-ms", type=int, default=1000)
    args = ap.parse_args()

    ok, reason = is_safe_readonly(args.command)
    if not ok and not args.allow_any:
        raise SystemExit(f"refusing command: {reason}")
    if DANGEROUS.search(args.command):
        raise SystemExit("refusing command with shell metacharacters/separators")

    packet = build_packet(args.command)
    print(f"command: {args.command!r}")
    print(f"packet length: {len(packet)} bytes")
    print(f"packet[0:64]: {packet[:64].hex()}")

    if not args.send:
        print("dry-run only; add --send to write to USB")
        return 0

    data = send_command(args.command, args.timeout_ms)
    print(f"read {len(data)} bytes from EP 0x{EP_CMD_IN:02x}")
    if data:
        print("hex:")
        print(data.hex())
        print("printable:")
        print(printable(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
