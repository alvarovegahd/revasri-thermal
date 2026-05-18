#!/usr/bin/env python3
"""Disassemble a chunk of libUVCCamera_dr.so and resolve every string literal
loaded via ADR, ADRP+ADD, or ADRP+LDR pairs. Outputs a clean list of (instruction
address, string address, string content) so we can see what was passed to each
function call.

The aim: find what string literals are loaded right before each call in
UVCCamera::executeCmd (the cmd1..cmd5 transfer sequence).

Ghidra's address for executeCmd is 0x15e320 but the ELF section .text starts
at 0x45fe0 with file size 0x60d90. We search the entire .text instead of
trusting Ghidra's address.
"""
from __future__ import annotations
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

LIB = Path("/home/alvaro/Documents/GitHub/revasri-thermal/linux_port/apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so")


def load_image() -> tuple[bytes, dict[str, dict]]:
    with open(LIB, "rb") as f:
        elf = ELFFile(f)
        sections = {}
        for sec in elf.iter_sections():
            sections[sec.name] = {
                "addr": sec["sh_addr"],
                "offset": sec["sh_offset"],
                "size": sec["sh_size"],
            }
        full_bytes = open(LIB, "rb").read()
    return full_bytes, sections


def read_string_at_vaddr(image: bytes, sections: dict, vaddr: int, maxlen: int = 256) -> str | None:
    """Read a NUL-terminated UTF-8 string at virtual address vaddr."""
    # Find the section that contains vaddr.
    for name, info in sections.items():
        if info["size"] == 0:
            continue
        if info["addr"] <= vaddr < info["addr"] + info["size"]:
            offset = info["offset"] + (vaddr - info["addr"])
            end = min(offset + maxlen, len(image))
            chunk = image[offset:end]
            nul = chunk.find(b"\x00")
            if nul < 0:
                return None
            data = chunk[:nul]
            if all(0x20 <= b < 0x7F or b in (0x09, 0x0A, 0x0D) for b in data) and len(data) >= 3:
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            return None
    return None


def disasm_text(image: bytes, sections: dict):
    text = sections[".text"]
    code = image[text["offset"] : text["offset"] + text["size"]]
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False
    return md.disasm(code, text["addr"]), text


def find_string_loads(image, sections, target_string: str):
    """Find every ADRP+ADD that resolves to the literal `target_string`."""
    # First, find the vaddr of the literal in .rodata.
    rodata = sections.get(".rodata")
    if not rodata or rodata["size"] == 0:
        return []
    rodata_bytes = image[rodata["offset"] : rodata["offset"] + rodata["size"]]
    needle = target_string.encode("utf-8")
    matches = []
    start = 0
    while True:
        i = rodata_bytes.find(needle, start)
        if i < 0:
            break
        matches.append(rodata["addr"] + i)
        start = i + 1
    return matches


def function_around(insns, address):
    """Crude: gather instructions in a 0x200-byte window around `address`."""
    out = []
    for ins in insns:
        if abs(ins.address - address) < 0x200:
            out.append(ins)
    return out


def main():
    image, sections = load_image()
    print(f"Loaded {len(image)} bytes; sections: {list(sections)[:10]}...")
    text = sections[".text"]
    print(f".text @ {text['addr']:#x}  size {text['size']:#x}  file offset {text['offset']:#x}")

    # 1. Find the address of the literal "cp /tmp/artosyn-upgrade-ars31.img ..."
    needle = "cp /tmp/artosyn-upgrade-ars31.img"
    addrs = find_string_loads(image, sections, needle)
    print(f"\nFound {needle!r} at vaddrs: {[hex(a) for a in addrs]}")

    if not addrs:
        return

    # 2. Disassemble .text, find string loads that reference that string,
    #    then dump surrounding instructions and other string loads.
    print("\nSearching for string-load instructions that load that string...")
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False

    # Walk linearly through the entire .text, tracking pending ADRP.
    code = image[text["offset"] : text["offset"] + text["size"]]
    pending = {}  # reg -> page address
    string_loads = []   # (pc, str_vaddr, string)
    def parse_imm(s: str) -> int:
        """Parse an ARM64 immediate like '#0x33000' or '#0xc2f'."""
        s = s.strip().lstrip("#").rstrip("]")
        return int(s, 0)

    for ins in md.disasm(code, text["addr"]):
        op = ins.mnemonic
        if op == "adr":
            try:
                _rd, imm = [p.strip() for p in ins.op_str.split(",")]
                target = parse_imm(imm)
                s = read_string_at_vaddr(image, sections, target)
                if s:
                    string_loads.append((ins.address, target, s))
            except Exception:
                pass
        elif op == "adrp":
            try:
                rd, imm = [p.strip() for p in ins.op_str.split(",")]
                pending[rd] = parse_imm(imm)
            except Exception:
                pass
        elif op == "add":
            parts = [p.strip() for p in ins.op_str.split(",")]
            if len(parts) == 3:
                rd, rn, imm_part = parts
                if rn in pending and imm_part.startswith("#"):
                    try:
                        target = pending[rn] + parse_imm(imm_part)
                        s = read_string_at_vaddr(image, sections, target)
                        if s:
                            string_loads.append((ins.address, target, s))
                    except Exception:
                        pass
                # Only drop pending if rd != rn (i.e., we wrote to a different reg)
                if rd != rn:
                    pending.pop(rn, None)
        elif op == "ldr":
            # ldr xN, [xM, #imm]  or  ldr xN, [xM]
            parts = [p.strip() for p in ins.op_str.split(",")]
            if len(parts) >= 2:
                # parts[1] looks like '[xN' and parts[2] like '#imm]' or absent
                base_part = parts[1].lstrip("[").rstrip("]")
                if base_part in pending:
                    imm_val = 0
                    if len(parts) >= 3:
                        try:
                            imm_val = parse_imm(parts[2])
                        except Exception:
                            imm_val = 0
                    try:
                        target = pending[base_part] + imm_val
                        s = read_string_at_vaddr(image, sections, target)
                        if s:
                            string_loads.append((ins.address, target, s))
                    except Exception:
                        pass

    print(f"\nTotal string loads found in .text: {len(string_loads)}")

    # Find string loads of the cmd1 literal and look at what other strings load nearby.
    target_vaddr = addrs[0]
    nearby = []
    for pc, sva, s in string_loads:
        if sva == target_vaddr:
            nearby.append((pc, sva, s))
    print(f"\n{len(nearby)} site(s) reference {needle!r}:")
    for pc, sva, s in nearby:
        print(f"  pc={pc:#x} -> {sva:#x} {s!r}")
        # Find all string loads within ±0x400 bytes of this pc.
        window = [(p, v, t) for (p, v, t) in string_loads if abs(p - pc) <= 0x800]
        print(f"  Strings loaded within ±0x800 bytes of this site ({len(window)} total):")
        for p, v, t in window:
            print(f"    pc={p:#x}  ->  {v:#x}  {t!r}")
        print()


if __name__ == "__main__":
    main()
