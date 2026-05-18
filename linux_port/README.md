# Linux port of the REVASRI Thermal Camera

Goal: take the Android APK that ships with this camera (`Camera+App_v1.0.2.25121301.apk`)
and reproduce just enough of its USB protocol on Linux to capture and decode frames.

## Hardware

USB device `04b4:000a` (Cypress FX2/FX3 bridge), serial `C501000-ABI0608`, vendor
class (no UVC). Two bulk interfaces:

| Interface | Alt | EP IN  | EP OUT | Packet | Purpose (guess)        |
|-----------|-----|--------|--------|--------|------------------------|
| 0         | 1   | `0x82` | `0x06` | 512 B  | frame / data stream    |
| 1         | 0   | `0x81` | `0x01` |  64 B  | control / commands     |

## Pipeline

Run scripts in order. Each one is idempotent — re-running won't break a fresh checkout.

| Step                              | What it does                                                              |
|-----------------------------------|---------------------------------------------------------------------------|
| `00_create_env.sh`                | Creates the `thermal-cam` conda env and installs Python deps.             |
| `01_install_udev.sh`              | Installs the udev rule so the camera is accessible without sudo.          |
| `02_extract_apk.sh`               | Unzips the APK into `apk_extracted/`.                                     |
| `03_strings_native.sh`            | Runs `strings` on the vendor `_dr` native libs; writes `strings/*.txt`.   |
| `04_install_jadx.sh`              | Downloads jadx (Java decompiler) into `tools/jadx/`.                      |
| `05_decompile_dex.sh`             | Runs jadx on the APK; output in `decompiled/`.                            |
| `06_grep_usb_protocol.sh`         | Greps decompiled sources for USB calls and command opcodes.               |
| `07_capture_frames.py`            | Captures N raw direct-bulk frames (`100436` B each). Writes `captures/*.bin`.|
| `08_view_frame.py`                | Diagnostic viewer for one captured frame (`256x196` BE uint16 body).      |
| `09_capture_stream.py`            | Records a continuous raw stream for N seconds; for stimulus experiments.  |
| `10_analyze_stimulus.py`          | Finds frame period + per-byte response via autocorrelation / stimulus.    |
| `11_probe_pixel_format.py`        | Brute-force image layout / endian / mask variants.                        |
| `12_nuc_diff.py`                  | Splits a stimulus stream into hot/cold groups and visualizes their diff.  |
| `13_anatomy.py`                   | Inspects frame magic, per-pixel median, and frame-to-frame motion.        |
| `14_scan_order.py`                | Tests candidate scan orders using the temporal median map.                |
| `15_best_face.py`                 | Picks a clear hot-stimulus frame after software NUC subtraction.          |
| `16_calibrate_nuc.py`             | Captures a software NUC reference (`calibration/nuc_ref.npy`).            |
| `17_live_view.py`                 | Experimental live viewer with software NUC and outlier skipping.          |
| `18_trailer_analysis.py`          | Plots the 84 extra bytes observed after each direct-bulk body.            |
| `19_install_ghidra.sh`            | Installs Ghidra locally under `tools/`.                                   |
| `20_analyze_lib.sh`               | Runs headless Ghidra on `libUVCCamera_dr.so`.                             |
| `21_extract_jni_table.py`         | Recovers dynamic JNI method names/signatures/function addresses.          |
| `22_decompile_jni_targets.sh`     | Decompiles selected JNI targets from the native library.                  |
| `23_decompile_native_patterns.sh` | Decompiles native helper patterns (`ALCall`, `UVCPreviewIR`, etc.).       |
| `24_frame_browser.py`             | Interactive frame browser/annotator for manual good/noise labels.        |
| `25_phase_probe.py`               | Tries byte offsets/orientation to find the real frame/body boundary.      |
| `26_row_header_probe.py`          | Tests native `row+6` flag / `row+12` payload header hypothesis.           |
| `27_list_device_commands.py`      | Static, no-device scan for embedded shell/`ir_cmd` command strings.       |
| `28_usb_shell_probe.py`           | Guarded experimental read-only probe for the USB command channel.         |

Re-running the setup pipeline from scratch:

```bash
for s in linux_port/0[0-6]_*.sh; do bash "$s" || break; done
```

Once setup is done, capture and view:

```bash
conda activate thermal-cam
python linux_port/07_capture_frames.py -n 5 -o linux_port/captures
python linux_port/08_view_frame.py linux_port/captures/frame_YYYYMMDD_HHMMSS_000.bin --show
```

For the current `stim_face.bin`, offset probing found the best visual alignment
around byte `414`:

```bash
python linux_port/24_frame_browser.py linux_port/captures/stim_face.bin --offset 414 --flip-y
python linux_port/07_capture_frames.py -n 5 -o linux_port/captures --phase-offset 414
python linux_port/17_live_view.py --phase-offset 414
```

See [FINDINGS.md](FINDINGS.md) for the reverse-engineering notes that drove the
choice of resolution, endpoint, and altsetting.

Current important caveat: direct-bulk capture is not the same as the Android
display path. The APK's native code sends the raw 256x196 buffer through
`ALCall`, which performs NUC, bad-pixel correction, stripe removal, temporal
filtering, and palette mapping. The Python viewer is useful for inspection, but
the final Linux port should reproduce or call that processing path.
