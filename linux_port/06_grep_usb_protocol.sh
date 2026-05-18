#!/usr/bin/env bash
# Search decompiled Java for the USB protocol surface:
#   - sendCmd / executeCmd / cmd1..cmd5 — the init byte sequences
#   - frame width/height/format constants
#   - UVCCamera open / setControlInterface / setPreviewSize calls
#   - the com.serenegiant.usbdr package (vendor fork of saki4510t/UVCCamera)
# Writes hit lists to linux_port/findings/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/decompiled"
OUT="$HERE/findings"

if [[ ! -d "$SRC" ]]; then
    echo "[06] no decompiled source at $SRC — run 05_decompile_dex.sh" >&2
    exit 1
fi

mkdir -p "$OUT"

# Helper: search Java only, exclude noisy android/androidx stubs.
gj() {
    grep -RIn --include='*.java' \
        --exclude-dir=android --exclude-dir=androidx \
        --exclude-dir=kotlin --exclude-dir=kotlinx \
        "$@" "$SRC" 2>/dev/null || true
}

echo "[06] usbdr (vendor package) files:"
find "$SRC" -path '*/com/serenegiant/usbdr/*' -name '*.java' | tee "$OUT/usbdr_files.txt" | wc -l | xargs printf "  %s files\n"

echo
echo "[06] sendCmd / executeCmd hits -> findings/cmd_hits.txt"
gj -E '\b(sendCmd|executeCmd|nativeSendCmd|cmd[1-9])\b' > "$OUT/cmd_hits.txt"
wc -l < "$OUT/cmd_hits.txt" | xargs printf "  %s lines\n"

echo "[06] frame size / format constants -> findings/frame_size.txt"
gj -E '(setPreviewSize|frameWidth|frameHeight|256\s*,\s*192|160\s*,\s*120|640\s*,\s*512|FRAME_FORMAT|UVC_FRAME_FORMAT)' > "$OUT/frame_size.txt"
wc -l < "$OUT/frame_size.txt" | xargs printf "  %s lines\n"

echo "[06] UVCCamera open/init surface -> findings/uvc_init.txt"
gj -E '\b(UVCCamera|openCamera|setControlInterface|setStatusCallback|setButtonCallback|setPreviewDisplay|startPreview|startTemperature|setTemperatureCallback)\b' > "$OUT/uvc_init.txt"
wc -l < "$OUT/uvc_init.txt" | xargs printf "  %s lines\n"

echo "[06] hex byte arrays that look like commands -> findings/byte_arrays.txt"
gj -E 'new byte\[\] *\{[^}]*0x[0-9A-Fa-f]+' > "$OUT/byte_arrays.txt"
wc -l < "$OUT/byte_arrays.txt" | xargs printf "  %s lines\n"

echo "[06] native bindings (System.loadLibrary, native method decls) -> findings/native_bindings.txt"
gj -E '(System\.loadLibrary|public *native|private *native|static *native)' > "$OUT/native_bindings.txt"
wc -l < "$OUT/native_bindings.txt" | xargs printf "  %s lines\n"

echo
echo "[06] done. Browse $OUT/*.txt"
