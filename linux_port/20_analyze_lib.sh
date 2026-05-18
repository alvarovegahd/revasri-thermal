#!/usr/bin/env bash
# Run Ghidra headless analysis on libUVCCamera_dr.so, then run extract_cmds.py
# (the Jython script) inside Ghidra to dump decompiled C for functions that
# reference the cmd1..cmd5 transfer strings.
#
# Output: linux_port/decompiled_native/cmd_functions.c
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHIDRA="$HERE/tools/ghidra"
LIB="$HERE/apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so"
PROJECT_DIR="$HERE/tools/ghidra_project"
OUT_DIR="$HERE/decompiled_native"
SCRIPT_DIR="$HERE/scripts_ghidra"

if [[ ! -x "$GHIDRA/support/analyzeHeadless" ]]; then
    echo "[20] Ghidra not installed — run 19_install_ghidra.sh first" >&2
    exit 1
fi
if [[ ! -f "$LIB" ]]; then
    echo "[20] $LIB not found — run 02_extract_apk.sh first" >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate thermal-cam

mkdir -p "$PROJECT_DIR" "$OUT_DIR"
export OUTPUT_ROOT="$OUT_DIR"

echo "[20] running Ghidra headless on $(basename "$LIB")"
echo "[20] this takes ~3-8 min on first run (auto-analysis is the slow part)"
"$GHIDRA/support/analyzeHeadless" \
    "$PROJECT_DIR" thermal_proj \
    -import "$LIB" \
    -overwrite \
    -scriptPath "$SCRIPT_DIR" \
    -postScript extract_cmds.py 2>&1 \
    | tail -30

echo "[20] done. Output: $OUT_DIR/"
ls -la "$OUT_DIR/" 2>/dev/null || true
