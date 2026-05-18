#!/usr/bin/env python3
"""Statically list command strings embedded in libUVCCamera_dr.so.

The native library contains a `sendCmd(libusb_device_handle*, char*, int)` helper
that sends shell-like command strings to the embedded device. This script does
not talk to the camera. It only inspects the APK's ARM64 library and reports:

  * command-looking strings in .rodata
  * every BL call to the known sendCmd function
  * string literals loaded near those calls

This is the safe first step before trying any live USB command probe.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from elftools.elf.elffile import ELFFile

DEFAULT_LIB = Path(__file__).resolve().parent / "apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so"
SENDCMD_VA = 0x464C0


COMMAND_RE = re.compile(
    r"^(ir_cmd|cp |touch |sync$|reboot$|rm |mv |cat |ls |echo |uname|id$|pwd$|/)"
)


def load_elf(path: Path) -> tuple[bytes, dict[str, dict]]:
    with path.open("rb") as f:
        elf = ELFFile(f)
        sections = {
            sec.name: {
                "addr": sec["sh_addr"],
                "offset": sec["sh_offset"],
                "size": sec["sh_size"],
            }
            for sec in elf.iter_sections()
        }
    return path.read_bytes(), sections


def section_bytes(image: bytes, sections: dict[str, dict], name: str) -> tuple[int, bytes]:
    info = sections[name]
    return info["addr"], image[info["offset"] : info["offset"] + info["size"]]


def strings_in_rodata(image: bytes, sections: dict[str, dict]) -> list[tuple[int, str]]:
    base, data = section_bytes(image, sections, ".rodata")
    out: list[tuple[int, str]] = []
    for m in re.finditer(rb"[\x20-\x7e]{3,}\x00", data):
        raw = m.group(0)[:-1]
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        out.append((base + m.start(), s))
    return out


def read_string_at(image: bytes, sections: dict[str, dict], vaddr: int, maxlen: int = 256) -> str | None:
    for info in sections.values():
        start = info["addr"]
        end = start + info["size"]
        if info["size"] and start <= vaddr < end:
            off = info["offset"] + (vaddr - start)
            raw = image[off : min(off + maxlen, len(image))]
            nul = raw.find(b"\x00")
            if nul < 0:
                return None
            raw = raw[:nul]
            if len(raw) < 3 or any(not (0x20 <= b < 0x7F) for b in raw):
                return None
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return None
    return None


def parse_imm(s: str) -> int:
    return int(s.strip().lstrip("#").rstrip("]"), 0)


def disassemble_text(image: bytes, sections: dict[str, dict]):
    text = sections[".text"]
    code = image[text["offset"] : text["offset"] + text["size"]]
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False
    return list(md.disasm(code, text["addr"]))


def recover_string_loads(image: bytes, sections: dict[str, dict], insns) -> list[tuple[int, str]]:
    pending: dict[str, int] = {}
    loads: list[tuple[int, str]] = []
    for ins in insns:
        op = ins.mnemonic
        if op == "adr":
            try:
                _rd, imm = [p.strip() for p in ins.op_str.split(",", 1)]
                s = read_string_at(image, sections, parse_imm(imm))
                if s:
                    loads.append((ins.address, s))
            except Exception:
                pass
        elif op == "adrp":
            try:
                rd, imm = [p.strip() for p in ins.op_str.split(",", 1)]
                pending[rd] = parse_imm(imm)
            except Exception:
                pass
        elif op == "add":
            parts = [p.strip() for p in ins.op_str.split(",")]
            if len(parts) == 3:
                rd, rn, imm = parts
                if rn in pending and imm.startswith("#"):
                    s = read_string_at(image, sections, pending[rn] + parse_imm(imm))
                    if s:
                        loads.append((ins.address, s))
                if rd != rn:
                    pending.pop(rn, None)
        elif op == "ldr":
            parts = [p.strip() for p in ins.op_str.split(",")]
            if len(parts) >= 2:
                base = parts[1].lstrip("[").rstrip("]")
                if base in pending:
                    imm = 0
                    if len(parts) >= 3:
                        try:
                            imm = parse_imm(parts[2])
                        except Exception:
                            imm = 0
                    s = read_string_at(image, sections, pending[base] + imm)
                    if s:
                        loads.append((ins.address, s))
    return loads


def find_sendcmd_calls(insns, target: int) -> list[int]:
    calls = []
    for ins in insns:
        if ins.mnemonic != "bl":
            continue
        try:
            dest = parse_imm(ins.op_str)
        except Exception:
            continue
        if dest == target:
            calls.append(ins.address)
    return calls


def classify(s: str) -> str:
    if s.startswith("ir_cmd "):
        return "roi/overlay command"
    if s in {"sync", "reboot"} or s.startswith(("cp ", "touch ")):
        return "firmware/system command - do not run casually"
    if COMMAND_RE.search(s):
        return "shell-like string"
    return "nearby string"


def main() -> int:
    ap = argparse.ArgumentParser(description="List embedded device command strings")
    ap.add_argument("--lib", type=Path, default=DEFAULT_LIB)
    ap.add_argument("--sendcmd-va", type=lambda x: int(x, 0), default=SENDCMD_VA)
    ap.add_argument("--window", type=lambda x: int(x, 0), default=0x180,
                    help="PC window around sendCmd calls for nearby string loads")
    args = ap.parse_args()

    image, sections = load_elf(args.lib)
    strings = strings_in_rodata(image, sections)
    command_strings = [(va, s) for va, s in strings if COMMAND_RE.search(s)]

    print(f"Library: {args.lib}")
    print(f"Command-looking .rodata strings: {len(command_strings)}")
    for va, s in command_strings:
        print(f"  {va:#08x}  {classify(s):42s}  {s!r}")

    insns = disassemble_text(image, sections)
    loads = recover_string_loads(image, sections, insns)
    calls = find_sendcmd_calls(insns, args.sendcmd_va)
    print(f"\nCalls to sendCmd-like function at {args.sendcmd_va:#x}: {len(calls)}")

    seen_site = set()
    for call in calls:
        nearby = [(pc, s) for pc, s in loads if call - args.window <= pc <= call + args.window]
        if not nearby:
            continue
        key = (call, tuple(s for _, s in nearby))
        if key in seen_site:
            continue
        seen_site.add(key)
        print(f"\ncall pc={call:#08x}")
        for pc, s in nearby:
            print(f"  load pc={pc:#08x}  {classify(s):42s}  {s!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
