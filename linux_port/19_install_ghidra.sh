#!/usr/bin/env bash
# Install Ghidra (NSA's reverse-engineering suite) locally — no sudo, no
# system-wide changes. Ghidra needs Java 21+; we use the openjdk already
# installed in the `thermal-cam` conda env.
#
# Re-running is safe: skips download if the binary's already present.
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$HERE/tools"
GHIDRA_VER="${GHIDRA_VER:-11.2.1}"
GHIDRA_BUILD="${GHIDRA_BUILD:-20241105}"
GHIDRA_ZIP="ghidra_${GHIDRA_VER}_PUBLIC_${GHIDRA_BUILD}.zip"
GHIDRA_URL="https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VER}_build/${GHIDRA_ZIP}"
GHIDRA_DIR="$TOOLS/ghidra"

mkdir -p "$TOOLS"
if [[ -x "$GHIDRA_DIR/support/analyzeHeadless" ]]; then
    echo "[19] Ghidra already installed at $GHIDRA_DIR"
    exit 0
fi

tmpzip="$(mktemp --suffix=.zip)"
echo "[19] downloading Ghidra ${GHIDRA_VER} (~400 MB)"
curl -fL --progress-bar -o "$tmpzip" "$GHIDRA_URL"
echo "[19] extracting"
tmpdir="$(mktemp -d)"
unzip -q "$tmpzip" -d "$tmpdir"
mv "$tmpdir"/ghidra_* "$GHIDRA_DIR"
rm -rf "$tmpzip" "$tmpdir"
chmod +x "$GHIDRA_DIR/support/analyzeHeadless" "$GHIDRA_DIR/ghidraRun" 2>/dev/null || true

# Verify
# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate thermal-cam
echo "[19] Java available: $(java -version 2>&1 | head -1)"
echo "[19] Ghidra installed at $GHIDRA_DIR"
echo "[19] To launch the GUI:  $GHIDRA_DIR/ghidraRun"
echo "[19] Headless analyzer:  $GHIDRA_DIR/support/analyzeHeadless"
