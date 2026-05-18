#!/usr/bin/env bash
# Install jadx (Java decompiler) locally — no sudo, no system-wide changes.
#   - OpenJDK goes into the `thermal-cam` conda env.
#   - jadx release is unpacked under linux_port/tools/jadx/.
# Re-running is safe; both halves no-op if already present.
# NB: we deliberately don't `set -u` — some conda activate/deactivate hooks
# (e.g. openjdk's) reference vars that aren't set on first install and crash
# the script otherwise.
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$HERE/tools"
JADX_DIR="$TOOLS/jadx"
JADX_VERSION="${JADX_VERSION:-1.5.1}"
JADX_URL="https://github.com/skylot/jadx/releases/download/v${JADX_VERSION}/jadx-${JADX_VERSION}.zip"

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate thermal-cam

# 1. OpenJDK inside the conda env (idempotent).
if java -version >/dev/null 2>&1; then
    echo "[04] java already available: $(java -version 2>&1 | head -1)"
else
    echo "[04] installing openjdk into the thermal-cam env"
    conda install -y openjdk
fi

# 2. jadx release.
mkdir -p "$TOOLS"
if [[ -x "$JADX_DIR/bin/jadx" ]]; then
    echo "[04] jadx already present at $JADX_DIR"
else
    tmpzip="$(mktemp --suffix=.zip)"
    echo "[04] downloading jadx ${JADX_VERSION}"
    curl -fL --progress-bar -o "$tmpzip" "$JADX_URL"
    rm -rf "$JADX_DIR"
    mkdir -p "$JADX_DIR"
    unzip -q "$tmpzip" -d "$JADX_DIR"
    rm -f "$tmpzip"
    chmod +x "$JADX_DIR/bin/jadx" "$JADX_DIR/bin/jadx-gui" 2>/dev/null || true
fi

echo "[04] jadx version: $("$JADX_DIR/bin/jadx" --version 2>&1)"
echo "[04] done. Binary at $JADX_DIR/bin/jadx"
