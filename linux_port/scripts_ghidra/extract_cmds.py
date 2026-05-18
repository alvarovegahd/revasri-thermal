# -*- coding: utf-8 -*-
# Ghidra Jython script - DO NOT run directly. Invoked by 20_analyze_lib.sh.
#
# Goal: for libUVCCamera_dr.so, find every reference to the strings
# "cmd1 transfer", "cmd2 transfer", ..., "cmd5 transfer". For each, locate
# the calling function, decompile it, and dump:
#   - the decompiled C-like source
#   - any byte-array initializers in nearby bytes
#
# Writes results under linux_port/decompiled_native/ relative to the
# repo root passed via the OUTPUT_ROOT environment variable.

import os
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

OUT_DIR = os.environ.get("OUTPUT_ROOT", "/tmp")
if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)


def find_string_addresses(needle):
    """Return list of addresses where the literal `needle` lives in memory."""
    fm = currentProgram.getMemory()  # noqa: F821 (Ghidra binding)
    addrs = []
    # Walk all defined data; find strings whose value contains the needle.
    listing = currentProgram.getListing()  # noqa: F821
    data_iter = listing.getDefinedData(True)
    while data_iter.hasNext():
        d = data_iter.next()
        if d.hasStringValue():
            val = d.getValue()
            if val and needle in str(val):
                addrs.append(d.getAddress())
    return addrs


def function_containing(addr):
    fm = currentProgram.getFunctionManager()  # noqa: F821
    return fm.getFunctionContaining(addr)


def referring_functions(string_addr):
    """Return set of functions that contain a reference to this address."""
    ref_mgr = currentProgram.getReferenceManager()  # noqa: F821
    funcs = set()
    refs = ref_mgr.getReferencesTo(string_addr)
    for r in refs:
        f = function_containing(r.getFromAddress())
        if f is not None:
            funcs.add(f)
    return funcs


def decompile(func):
    di = DecompInterface()
    di.openProgram(currentProgram)  # noqa: F821
    res = di.decompileFunction(func, 60, ConsoleTaskMonitor())
    if res and res.decompileCompleted():
        return res.getDecompiledFunction().getC()
    return "<failed to decompile %s>" % func.getName()


found_any = False
out_path = os.path.join(OUT_DIR, "cmd_functions.c")
with open(out_path, "w") as fp:
    fp.write("// Decompiled functions referencing cmd1..cmd5 strings\n")
    fp.write("// Source: libUVCCamera_dr.so via Ghidra headless\n\n")
    for needle in ("cmd1 transfer", "cmd2 transfer", "cmd3 transfer",
                   "cmd4 transfer", "cmd5 transfer", "Cmd Transfer"):
        addrs = find_string_addresses(needle)
        fp.write("// ============ search: %r ============\n" % needle)
        fp.write("// found at: %s\n\n" % [str(a) for a in addrs])
        funcs_seen = set()
        for a in addrs:
            for f in referring_functions(a):
                if f.getEntryPoint() in funcs_seen:
                    continue
                funcs_seen.add(f.getEntryPoint())
                fp.write("// ----- %s @ %s -----\n" % (f.getName(), f.getEntryPoint()))
                fp.write(decompile(f))
                fp.write("\n\n")
                found_any = True

print("[extract_cmds] wrote %s" % out_path)
if not found_any:
    print("[extract_cmds] WARNING: no matching strings found")

# Also: dump all functions whose names contain "Cmd" or "sendCmd" or "executeCmd".
out_path2 = os.path.join(OUT_DIR, "cmd_named_functions.c")
fm = currentProgram.getFunctionManager()  # noqa: F821
with open(out_path2, "w") as fp:
    fp.write("// All functions whose name matches /(Cmd|cmd|exec)/\n\n")
    for f in fm.getFunctions(True):
        name = f.getName()
        if any(k in name for k in ("Cmd", "cmd", "execute", "send")):
            fp.write("// ----- %s @ %s -----\n" % (name, f.getEntryPoint()))
            fp.write(decompile(f))
            fp.write("\n\n")
print("[extract_cmds] wrote %s" % out_path2)
