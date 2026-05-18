# -*- coding: utf-8 -*-
# Ghidra Jython script - invoked manually/headless.
#
# Decompile C++ functions whose names match camera-control / conversion terms.

import os
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

OUT_DIR = os.environ.get("OUTPUT_ROOT", "/tmp")
OUT = os.path.join(OUT_DIR, "native_named_patterns.c")

PATTERNS = [
    "startTemp",
    "stopTemp",
    "startCapture",
    "stopCapture",
    "whenShutRefresh",
    "setTempRange",
    "setShutterFix",
    "setCameraLens",
    "setTempDiv",
    "setTmpParams",
    "getByteArray",
    "setLastFourLine",
    "processNUC",
    "NUC",
    "RGB",
    "ALCall",
    "F1_crop",
    "thermometry",
]


def decompiler():
    di = DecompInterface()
    di.openProgram(currentProgram)  # noqa: F821
    return di


def decompile(di, func):
    res = di.decompileFunction(func, 90, ConsoleTaskMonitor())
    if res and res.decompileCompleted():
        return res.getDecompiledFunction().getC()
    return "/* failed to decompile %s */\n" % func.getName(True)


di = decompiler()
fm = currentProgram.getFunctionManager()  # noqa: F821
matches = []
for f in fm.getFunctions(True):
    full = f.getName(True)
    short = f.getName()
    if any(p in full or p in short for p in PATTERNS):
        matches.append(f)

with open(OUT, "w") as fp:
    fp.write("// Decompiled native functions matching camera-control/conversion patterns\n\n")
    for f in matches:
        fp.write("// ============================================================\n")
        fp.write("// %s @ %s\n" % (f.getName(True), f.getEntryPoint()))
        fp.write(decompile(di, f))
        fp.write("\n\n")

print("[decompile_named_patterns] wrote %s (%d functions)" % (OUT, len(matches)))
