#!/usr/bin/env python3
"""Disassemble UVCCamera::executeCmd (and surrounding init) and resolve every
string literal address loaded by ADRP/ADD or ADRP/LDR sequences.

This bypasses Ghidra's decompiler simplifications and gives us the raw list of
strings actually referenced by the function — i.e., the cmd1..cmd5 shell
command literals we need.
"""
from __future__ import annotations
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

LIB = Path("/home/alvaro/Documents/GitHub/revasri-thermal/linux_port/apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so")
# Functions of interest (Ghidra-reported addresses)
FUNCS = [
    ("UVCCamera::executeCmd_1", 0x0015e320),
    ("UVCCamera::executeCmd_2", 0x001a7f30),
]
SCAN_BYTES = 0x1000   # plenty for ~1000 instructions per function


def read_section(elf, addr, length):
    for seg in elf.iter_segments():
        seg_start = seg["p_vaddr"]
        seg_end = seg_start + seg["p_filesz"]
        if seg_start <= addr and addr + length <= seg_end:
            offset = seg["p_offset"] + (addr - seg_start)
            return elf.stream.read(offset, length) if hasattr(elf.stream, "read") else None
    return None


def read_at(stream, addr, length):
    """Read `length` bytes at virtual address `addr` from the loaded ELF."""
    with open(LIB, "rb") as f:
        elf = ELFFile(f)
        for seg in elf.iter_segments():
            seg_start = seg["p_vaddr"]
            seg_end = seg_start + seg["p_filesz"]
            if seg_start <= addr and addr + length <= seg_end:
                offset = seg["p_offset"] + (addr - seg_start)
                f.seek(offset)
                return f.read(length)
    return None


def read_string(addr, max_len=256):
    data = read_at(None, addr, max_len)
    if data is None:
        return None
    # Cut at first NUL
    n = data.find(b"\x00")
    if n >= 0:
        data = data[:n]
    try:
        s = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Only return if printable-ish
    if all(0x20 <= b < 0x7F or b in (0x09, 0x0A, 0x0D) for b in data) and len(s) >= 4:
        return s
    return None


def disasm_function(name, addr, length):
    print(f"\n========== {name} @ {addr:#x} ==========")
    code = read_at(None, addr, length)
    if code is None:
        print("  could not read function bytes")
        return
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True

    pending_adrp = {}    # reg -> page address
    string_refs = []

    insns = list(md.disasm(code, addr))
    for ins in insns:
        op = ins.mnemonic
        if op == "adrp":
            try:
                regs = ins.op_str.split(",")
                rd = regs[0].strip()
                page = int(regs[1].strip(), 0)
                pending_adrp[rd] = page
            except Exception:
                pass
        elif op in ("add", "ldr"):
            parts = [p.strip() for p in ins.op_str.split(",")]
            if len(parts) >= 3:
                rd = parts[0]
                rn = parts[1]
                imm_part = parts[2]
                if rn in pending_adrp:
                    try:
                        imm = int(imm_part.lstrip("#").rstrip("]"), 0)
                        target = pending_adrp[rn] + imm
                        s = read_string(target)
                        if s:
                            string_refs.append((ins.address, target, s))
                    except Exception:
                        pass
        elif op == "bl":
            # Print the BL with surrounding loaded-string context.
            for prev_addr, str_addr, s in string_refs[-5:]:
                print(f"    {prev_addr:#08x}: -> {str_addr:#08x} {s!r}")
            print(f"  {ins.address:#08x}: BL {ins.op_str}")
            string_refs = []   # reset for next BL
            pending_adrp = {}

    if string_refs:
        print("  (trailing string refs after last BL)")
        for prev_addr, str_addr, s in string_refs:
            print(f"    {prev_addr:#08x}: -> {str_addr:#08x} {s!r}")


for name, addr in FUNCS:
    disasm_function(name, addr, SCAN_BYTES)
