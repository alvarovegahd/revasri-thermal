#!/usr/bin/env bash
# Unzip the vendor APK into linux_port/apk_extracted/ for offline inspection.
# An APK is just a zip; this gives us the dex files, native .so libs, manifest,
# and assets we'll dig into in later steps.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APK_DEFAULT="$HOME/Downloads/Camera+App_v1.0.2.25121301.apk"
APK="${APK_PATH:-$APK_DEFAULT}"
OUT="$HERE/apk_extracted"

if [[ ! -f "$APK" ]]; then
    echo "[02] APK not found at: $APK" >&2
    echo "[02] override with: APK_PATH=/path/to/file.apk $0" >&2
    exit 1
fi

if [[ -d "$OUT" && -f "$OUT/AndroidManifest.xml" ]]; then
    echo "[02] already extracted at $OUT — skipping"
else
    mkdir -p "$OUT"
    echo "[02] extracting $APK -> $OUT"
    unzip -q -o "$APK" -d "$OUT"
fi

echo "[02] dex files:"
ls -lh "$OUT"/classes*.dex 2>/dev/null | awk '{print "  ", $5, $9}'

echo "[02] native libs (arm64-v8a):"
ls -lh "$OUT"/lib/arm64-v8a/*.so 2>/dev/null | awk '{print "  ", $5, $9}'

echo "[02] done."
