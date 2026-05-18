#!/usr/bin/env bash
# Dump printable strings from the vendor's _dr native libs. These small libs
# (libuvc_dr, libusb100_dr, libthermometry_dr, libUVCCamera_dr, libirOpencl)
# are the surface where the protocol lives. The strings tell us:
#   - JNI method names (the Java <-> native bridge)
#   - debug/log messages (often describe what a command does)
#   - register / opcode names
#   - libuvc and libusb fork hints (which upstream commits they're based on)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBDIR="$HERE/apk_extracted/lib/arm64-v8a"
OUTDIR="$HERE/strings"

if [[ ! -d "$LIBDIR" ]]; then
    echo "[03] missing $LIBDIR — run 02_extract_apk.sh first" >&2
    exit 1
fi

mkdir -p "$OUTDIR"

# The libs worth reading. Stock libs (TNN, opencv, ffmpeg, pdfium, mmkv,
# jpeg-turbo) are skipped — they're unrelated to the USB protocol.
LIBS=(
    libuvc_dr.so
    libusb100_dr.so
    libUVCCamera_dr.so
    libthermometry_dr.so
    libirOpencl.so
    libbasicAL.so
)

for lib in "${LIBS[@]}"; do
    src="$LIBDIR/$lib"
    dst="$OUTDIR/${lib%.so}.txt"
    if [[ ! -f "$src" ]]; then
        echo "[03] skip missing $lib"
        continue
    fi
    # -a all sections, -n 6 to skip noise. Keep order so addresses are stable.
    strings -a -n 6 "$src" > "$dst"
    echo "[03] $(wc -l < "$dst") strings -> $(realpath --relative-to="$HERE/.." "$dst")"
done

echo
echo "[03] quick hits — JNI method registrations (Java_*):"
grep -h '^Java_' "$OUTDIR"/*.txt 2>/dev/null | sort -u | head -20 || true

echo
echo "[03] quick hits — USB endpoint / transfer keywords:"
grep -hiE 'bulk_transfer|control_transfer|set_alt|claim_interface|libusb|vendor|0x82|0x06' \
    "$OUTDIR"/*.txt 2>/dev/null | sort -u | head -20 || true

echo
echo "[03] done. Browse $OUTDIR/*.txt for more."
