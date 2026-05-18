#!/usr/bin/env python3
"""Recover dynamic JNI registration tables from libUVCCamera_dr.so.

The .so is stripped, so functions such as `nativeCallInit` are not exported as
ELF symbols. Android libraries commonly register JNI methods with an array of:

    struct JNINativeMethod {
        const char *name;
        const char *signature;
        void *fnPtr;
    };

This script scans the ELF for those triples, resolves the name/signature
strings, and prints the native function pointer. That gives us the real ARM64
function addresses to inspect in Ghidra or via the Capstone helpers.
"""
from __future__ import annotations

from pathlib import Path
import struct

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection

LIB = Path(__file__).resolve().parent / "apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so"
OUT = Path(__file__).resolve().parent / "decompiled_native/jni_methods.tsv"

TARGET_NAMES = {
    "nativeCallInit",
    "nativeWhenShutRefresh",
    "nativeStartStopTemp",
    "nativeStartStopCapture",
    "nativeSetTempRange",
    "nativeSetShutterFix",
    "nativeSetCameraLens",
    "nativeSetTmpParams",
    "nativeSetUsbStream",
    "nativeNUC",
    "nativeRGBdata",
    "nativeNUCToTmp",
    "nativeGetTempData",
    "nativeGetByteArrayPicture",
    "nativeGetByteArrayPara",
    "nativeGetByteArrayTemperaturePara",
    "nativeSetLastFourLineDataCallBack",
    "nativeCopyToSurface",
    "nativeSetPalette",
    "nativeSetPaletteLXFX",
}


def load_elf():
    image = LIB.read_bytes()
    with LIB.open("rb") as f:
        elf = ELFFile(f)
        sections = []
        for sec in elf.iter_sections():
            sections.append(
                {
                    "name": sec.name,
                    "addr": int(sec["sh_addr"]),
                    "offset": int(sec["sh_offset"]),
                    "size": int(sec["sh_size"]),
                    "flags": int(sec["sh_flags"]),
                }
            )
        relocs = {}
        for sec in elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            for rel in sec.iter_relocations():
                # Android's stripped shared libs use RELATIVE relocations for
                # static pointer tables. At load time, the value at r_offset is
                # base + r_addend. Since this ELF is linked at base 0, r_addend
                # is the useful virtual address.
                addend = rel.entry.get("r_addend", None)
                if addend is not None:
                    relocs[int(rel.entry["r_offset"])] = int(addend)
    return image, sections, relocs


def va_to_offset(sections, va: int) -> int | None:
    for sec in sections:
        if sec["size"] and sec["addr"] <= va < sec["addr"] + sec["size"]:
            return sec["offset"] + (va - sec["addr"])
    return None


def read_cstr(image: bytes, sections, va: int, maxlen: int = 512) -> str | None:
    off = va_to_offset(sections, va)
    if off is None or off >= len(image):
        return None
    end = min(len(image), off + maxlen)
    nul = image.find(b"\0", off, end)
    if nul < 0 or nul == off:
        return None
    data = image[off:nul]
    if not all((0x20 <= b < 0x7F) or b in (9, 10, 13) for b in data):
        return None
    try:
        return data.decode("ascii")
    except UnicodeDecodeError:
        return None


def is_code_ptr(sections, va: int) -> bool:
    for sec in sections:
        if sec["name"] == ".text" and sec["addr"] <= va < sec["addr"] + sec["size"]:
            return True
    return False


def read_ptr(image: bytes, relocs: dict[int, int], va: int, off: int) -> int:
    if va in relocs:
        return relocs[va]
    return struct.unpack_from("<Q", image, off)[0]


def scan_methods(image: bytes, sections, relocs) -> list[tuple[str, str, int, str, int]]:
    rows = []
    # Scan writable/relro data sections and rodata; registration tables can land
    # in .data.rel.ro or .rodata depending on linker options.
    scan_sections = [
        sec for sec in sections
        if sec["size"] >= 24 and sec["name"] in {".rodata", ".data", ".data.rel.ro", ".got", ".got.plt"}
    ]
    for sec in scan_sections:
        start = sec["offset"]
        stop = sec["offset"] + sec["size"] - 24
        for off in range(start, stop + 1, 8):
            table_va = sec["addr"] + (off - sec["offset"])
            name_ptr = read_ptr(image, relocs, table_va, off)
            sig_ptr = read_ptr(image, relocs, table_va + 8, off + 8)
            fn_ptr = read_ptr(image, relocs, table_va + 16, off + 16)
            name = read_cstr(image, sections, name_ptr)
            if not name or not name.startswith("native"):
                continue
            sig = read_cstr(image, sections, sig_ptr)
            if not sig or "(" not in sig:
                continue
            if not is_code_ptr(sections, fn_ptr):
                continue
            rows.append((name, sig, fn_ptr, sec["name"], table_va))
    # Deduplicate identical triples while keeping stable order.
    seen = set()
    uniq = []
    for row in rows:
        key = row[:3]
        if key not in seen:
            seen.add(key)
            uniq.append(row)
    return uniq


def main() -> int:
    image, sections, relocs = load_elf()
    rows = scan_methods(image, sections, relocs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        f.write("name\tsignature\tfn_va\ttable_section\ttable_va\n")
        for name, sig, fn, sec, table_va in sorted(rows, key=lambda r: (r[0], r[2])):
            f.write(f"{name}\t{sig}\t0x{fn:x}\t{sec}\t0x{table_va:x}\n")

    print(f"recovered {len(rows)} JNI methods")
    print(f"wrote {OUT.relative_to(Path.cwd()) if OUT.is_relative_to(Path.cwd()) else OUT}")
    print()
    print("targets:")
    by_name = {}
    for row in rows:
        by_name.setdefault(row[0], []).append(row)
    for target in sorted(TARGET_NAMES):
        matches = by_name.get(target, [])
        if not matches:
            print(f"  {target:<38} MISSING")
            continue
        for name, sig, fn, sec, table_va in matches:
            print(f"  {name:<38} {sig:<45} fn=0x{fn:x} table={sec}@0x{table_va:x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
