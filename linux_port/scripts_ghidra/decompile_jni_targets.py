# -*- coding: utf-8 -*-
# Ghidra Jython script - invoked by 22_decompile_jni_targets.sh.
#
# Reads linux_port/decompiled_native/jni_methods.tsv and decompiles selected
# JNI target functions. The JNI table gives ELF virtual addresses; Ghidra
# imported this library at image base 0x100000, so the script tries both
# address forms.

import os
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

OUT_DIR = os.environ.get("OUTPUT_ROOT", "/tmp")
TSV = os.environ.get("JNI_TSV", os.path.join(OUT_DIR, "jni_methods.tsv"))
OUT = os.path.join(OUT_DIR, "jni_target_functions.c")

TARGETS = set([
    "nativeCallInit",
    "nativeWhenShutRefresh",
    "nativeStartStopTemp",
    "nativeStartStopCapture",
    "nativeSetTempRange",
    "nativeSetShutterFix",
    "nativeSetCameraLens",
    "nativeSetTmpParams",
    "nativeSetTempDiv",
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
])


def decompiler():
    di = DecompInterface()
    di.openProgram(currentProgram)  # noqa: F821
    return di


def function_for_elf_va(elf_va):
    fm = currentProgram.getFunctionManager()  # noqa: F821
    for candidate in (elf_va, elf_va + 0x100000):
        addr = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(candidate)  # noqa: F821
        f = fm.getFunctionContaining(addr)
        if f is not None:
            return f, candidate
        f = fm.getFunctionAt(addr)
        if f is not None:
            return f, candidate
    return None, None


def decompile(di, func):
    res = di.decompileFunction(func, 90, ConsoleTaskMonitor())
    if res and res.decompileCompleted():
        return res.getDecompiledFunction().getC()
    return "/* failed to decompile %s */\n" % func.getName()


def read_targets():
    rows = []
    with open(TSV, "r") as fp:
        header = fp.readline()
        for line in fp:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            name, sig, fn_va, sec, table_va = parts[:5]
            if name in TARGETS or name.startswith("nativeStartStopCapture"):
                rows.append((name, sig, int(fn_va, 16), sec, table_va))
    return rows


rows = read_targets()
di = decompiler()
seen = set()
with open(OUT, "w") as fp:
    fp.write("// Decompiled selected JNI target functions from libUVCCamera_dr.so\n")
    fp.write("// Source table: %s\n\n" % TSV)
    for name, sig, elf_va, sec, table_va in rows:
        func, ghidra_va = function_for_elf_va(elf_va)
        fp.write("// ============================================================\n")
        fp.write("// %s %s  elf_va=0x%x  ghidra_va=%s  table=%s@%s\n" %
                 (name, sig, elf_va, ("0x%x" % ghidra_va) if ghidra_va else "NOT_FOUND", sec, table_va))
        if func is None:
            fp.write("// function not found\n\n")
            continue
        if func.getEntryPoint() in seen:
            fp.write("// same function already decompiled at %s\n\n" % func.getEntryPoint())
            continue
        seen.add(func.getEntryPoint())
        fp.write("// function %s @ %s\n" % (func.getName(), func.getEntryPoint()))
        fp.write(decompile(di, func))
        fp.write("\n\n")

print("[decompile_jni_targets] wrote %s (%d target rows)" % (OUT, len(rows)))
