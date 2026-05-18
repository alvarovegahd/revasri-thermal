#!/usr/bin/env bash
# Decompile selected named C++ methods from libUVCCamera_dr.so.
#
# Output: linux_port/decompiled_native/native_named_patterns.c
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHIDRA="$HERE/tools/ghidra"
PROJECT_DIR="$HERE/tools/ghidra_project"
PROJECT_NAME="thermal_proj"
LIB="$HERE/apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so"
SCRIPT_DIR="$HERE/scripts_ghidra"
OUT="$HERE/decompiled_native"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate thermal-cam
export HOME="$HERE/tools/ghidra_home"
export JAVA_TOOL_OPTIONS="-Duser.home=$HOME -Djava.awt.headless=true ${JAVA_TOOL_OPTIONS:-}"
mkdir -p "$HOME" "$OUT" "$PROJECT_DIR"

OUTPUT_ROOT="$OUT" "$GHIDRA/support/analyzeHeadless" \
  "$PROJECT_DIR" "$PROJECT_NAME" \
  -process "$(basename "$LIB")" \
  -postScript decompile_named_patterns.py \
  -scriptPath "$SCRIPT_DIR" \
  -noanalysis

echo "[23] wrote $OUT/native_named_patterns.c"
