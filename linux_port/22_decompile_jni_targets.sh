#!/usr/bin/env bash
# Decompile selected JNI target functions discovered by 21_extract_jni_table.py.
#
# Output: linux_port/decompiled_native/jni_target_functions.c
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GHIDRA="$HERE/tools/ghidra"
PROJECT_DIR="$HERE/tools/ghidra_project"
PROJECT_NAME="thermal_proj"
LIB="$HERE/apk_extracted/lib/arm64-v8a/libUVCCamera_dr.so"
SCRIPT_DIR="$HERE/scripts_ghidra"
OUT="$HERE/decompiled_native"
TSV="$OUT/jni_methods.tsv"

if [[ ! -x "$GHIDRA/support/analyzeHeadless" ]]; then
  echo "[22] missing Ghidra; run 19_install_ghidra.sh first" >&2
  exit 1
fi
if [[ ! -f "$TSV" ]]; then
  echo "[22] missing $TSV; run 21_extract_jni_table.py first" >&2
  exit 1
fi

mkdir -p "$OUT" "$PROJECT_DIR"

source "$HOME/miniconda3/etc/profile.d/conda.sh"
conda activate thermal-cam
export HOME="$HERE/tools/ghidra_home"
export JAVA_TOOL_OPTIONS="-Duser.home=$HOME -Djava.awt.headless=true ${JAVA_TOOL_OPTIONS:-}"
mkdir -p "$HOME"

OUTPUT_ROOT="$OUT" JNI_TSV="$TSV" "$GHIDRA/support/analyzeHeadless" \
  "$PROJECT_DIR" "$PROJECT_NAME" \
  -import "$LIB" \
  -overwrite \
  -postScript decompile_jni_targets.py \
  -scriptPath "$SCRIPT_DIR"

echo "[22] wrote $OUT/jni_target_functions.c"
