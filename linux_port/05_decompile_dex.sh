#!/usr/bin/env bash
# Run jadx on the APK to produce Java sources under linux_port/decompiled/.
# Idempotent: skips if output dir already has Java files.
# NB: no `set -u` — conda openjdk activate.d/deactivate.d hooks reference
# JAVA_HOME / CONDA_BACKUP_JAVA_HOME before defining them and crash otherwise.
set -eo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APK="${APK_PATH:-$HOME/Downloads/Camera+App_v1.0.2.25121301.apk}"
JADX="$HERE/tools/jadx/bin/jadx"
OUT="$HERE/decompiled"

if [[ ! -x "$JADX" ]]; then
    echo "[05] jadx not found at $JADX — run 04_install_jadx.sh" >&2
    exit 1
fi
if [[ ! -f "$APK" ]]; then
    echo "[05] APK not found at $APK" >&2
    exit 1
fi

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate thermal-cam   # for java

if find "$OUT/sources" -maxdepth 4 -name '*.java' 2>/dev/null | grep -q .; then
    echo "[05] decompiled output already exists at $OUT — skipping"
else
    mkdir -p "$OUT"
    echo "[05] decompiling (this takes ~1-3 min; jadx will report errors as warnings, that's fine)"
    # --no-res: skip resources (XML, drawables) — we only need code right now.
    # --show-bad-code: emit pseudo-Java even when decompile partially fails.
    "$JADX" -d "$OUT" --no-res --show-bad-code "$APK" 2>&1 | tail -10 || true
fi

echo "[05] Java file count: $(find "$OUT" -name '*.java' | wc -l)"
echo "[05] done. Sources under $OUT/sources/"
