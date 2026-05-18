# Reverse-engineering findings for REVASRI Thermal Camera

This is the current state of the Linux port work. The early notes in this repo
were intentionally exploratory; this file records the corrected findings after
the APK, live captures, and native `libUVCCamera_dr.so` were inspected.

## Hardware

| Field         | Value                                      |
|---------------|--------------------------------------------|
| VID:PID       | `04b4:000a` (Cypress FX-series USB bridge) |
| Mfr/Product   | `REVASRI` / `Thermal Camera`               |
| Serial        | `C501000-ABI0608`                          |
| Speed         | USB 2.0 High Speed                         |
| Device class  | composite, vendor-specific interfaces      |
| Stream EP     | interface 0 alt 1, bulk IN `0x82`, 512 B   |
| Other EPs     | bulk OUT `0x06`, bulk IN `0x81`, OUT `0x01`|

There is no `/dev/video*` node. The Android library uses `libusb`/`libuvc`, but
the USB descriptors are vendor-specific, so the normal Linux UVC driver does not
bind automatically.

## Android Stack

APK: `Camera+App_v1.0.2.25121301.apk`

App package: `com.inreii.temperaturemeasurement`

SDK package: `com.serenegiant.usbdr`, a vendor fork of `saki4510t/UVCCamera`.

Native libraries of interest:

| Library                 | Role |
|-------------------------|------|
| `libusb100_dr.so`       | libusb fork |
| `libuvc_dr.so`          | libuvc fork |
| `libUVCCamera_dr.so`    | JNI bridge, preview pipeline, image processing wrapper |
| `libthermometry_dr.so`  | thermometry helpers (`thermometryT4Line`, `CalcFixRaw`, etc.) |
| `libirOpencl.so`        | OpenCL image-processing support |

Assets identify the sensor family as 256x192 class:
`assets/cplus/filesx1_256_192_50fps_127_v0.1fta_250414.abd`.

## Direct-Bulk Capture

`07_capture_frames.py` reads from endpoint `0x82` after selecting interface 0,
altsetting 1.

The empirical frame period from a stimulus capture is:

```text
100436 bytes/frame
```

That is:

```text
100352 B body = 256 x 196 x uint16   (28 rows x 7 chunks)
    84 B extra: SEVEN 12-byte chunk headers, one at the start of each of the
                seven 14 348-byte bulk-IN transfers that make up a frame
                (see "Wire-Level Chunk Format" below). NOT a single trailer.
```

The body is big-endian uint16. Hot-glass stimulus experiments validated that the
body contains real thermal scene signal:

```bash
python linux_port/09_capture_stream.py --seconds 12 -o linux_port/captures/stim_face.bin
python linux_port/12_nuc_diff.py linux_port/captures/stim_face.bin
python linux_port/15_best_face.py linux_port/captures/stim_face.bin --image-rows 196
```

Those tests produced a clear localized hot-water-glass response. So the direct
bulk stream is not random data; it is a usable raw sensor stream, but it is not
yet equivalent to the Android display output.

## Wire-Level Chunk Format

Established by Frida-hooking `ioctl()` in the running Android app
(`29_frida_trace.py`).  The vendor lib bypasses the libusb wrappers and submits
URBs via raw `USBDEVFS_BULK` ioctls; that's why hooking `libusb_bulk_transfer`
produced zero events.  Hook target = `ioctl` filtered on request type `'U'`
(`(req >> 8) & 0xff == 0x55`).

Every bulk-IN on EP `0x82` is exactly **14 348 B** (5021/5021 in the
20260518_164206 trace).  Layout:

```text
offset  size  meaning
------  ----  -------
 0       4    timestamp / fine counter, LE u32 (semantics partly understood)
 4       2    session/stream ID, LE u16 (constant within a stream, varies per
              stream-start; seen as 0x000f, 0x0081, 0x0000 across captures)
 6       1    valid-row-group flag = 0x01     <-- same byte ALCall reads
 7       1    0x00 (padding)
 8       1    start row of this group: one of {1, 29, 57, 85, 113, 141, 169}
 9       3    0x000000
12    14336    28 rows * 256 px * 2 B of big-endian u16 samples
```

7 transfers x 14 348 B = 100 436 B per frame = the existing
`07_capture_frames.py` frame size.  Decode with:

```bash
python linux_port/31_decode_frida_chunks.py linux_port/captures/<file>.jsonl
```

Effective frame rate at the host varies: ~16 fps observed in steady-state
streaming, ~12 fps across a 60 s cold-start trace (Java code negotiates 50 fps
but the camera firmware caps it lower).

## Streaming Does Not Require Host-Side Init

Three cold-start traces (force-stop + unplug-replug, with Frida spawn-gating
arming hooks before the app's `onCreate`) show **no `cmd1..cmd5` sequence, no
`uvc_query_stream_ctrl`, no control transfers other than a single LangID
descriptor read**.  The camera firmware boots into streaming mode on USB
power-up; the app just opens `/dev/bus/usb/...` and starts reading.

The only OUT traffic during steady-state preview is a two-byte `80 00` on EP
`0x06`, repeated ~once every 2 seconds.  Likely a keepalive or shutter
sync.  Reproducing it on Linux is not required for plain raw capture.

## Corrected Native Frame Model

The key native path is `nativeCallInit(mode, width, height)`:

```text
mode == 0 -> ALCall(width, height)
mode == 1 -> F1_crop(), fixed 160x120 path
```

`ALCall::ALCall(width, height)` stores the input height, but allocates and
initializes the image-processing path with `height - 4`:

```text
uVar2 = (height - 4) * width
uVar3 = height * width
ImageProcessInit(width, height - 4)
```

For the 256x196 live path, the processor therefore expects a 256x196 input body
and produces/uses a 256x192 image region. The extra 4 rows are support data for
the vendor processing path, not rows we should simply crop by eye.

Important JNI targets recovered from the dynamic JNI table:

| JNI method | Native role |
|------------|-------------|
| `nativeCallInit(III)I` | constructs `ALCall` or `F1_crop` |
| `nativeRGBdata([B)[B` | `getNUCdata(input)` then `getRGBdata(...)`, returns 256x192x4 display bytes |
| `nativeNUCToTmp([B)[F` | converts raw bytes to calibrated per-pixel float temperatures |
| `nativeGetTempData([B)[F` | temperature extraction path |
| `nativeWhenShutRefresh(J)V` | sets a refresh/shutter flag in `UVCPreviewIR` |
| `nativeStartStopTemp(JI)I` | starts/stops the temperature thread |
| `nativeGetByteArrayTemperaturePara(JI)[B` | extracts a 128-byte parameter block from the frame buffer |
| `nativeGetByteArrayPara(JI)[B` | extracts a second 128-byte parameter block |

The Android display is not a simple `imshow(raw)`. The native `ALCall` pipeline
does at least:

- NUC extraction/processing
- bad-pixel handling
- K/B adjustment
- stripe removal
- temporal filtering
- palette mapping
- thermometry using `libthermometry_dr.so`

That is why a single direct-bulk frame looks noisy in Python even though
hot/cold stimulus subtraction reveals the correct scene.

## `executeCmd` Is Not Preview Init

Early string analysis suggested `cmd1..cmd5` might be the camera startup
sequence. Ghidra showed that was wrong.

`UVCCamera::executeCmd()` is the firmware-update command sequence. It sends
shell command strings over a separate command channel. The first command is:

```text
cp /tmp/artosyn-upgrade-ars31.img /storage/artosyn-upgrade-ars31.img
```

Other nearby command strings include:

```text
sync
touch /usrdata/sirius-clean-system-flag
reboot
```

Do not call this path for normal streaming. It is not needed for direct-bulk raw
capture and may alter/reboot the embedded device.

The shell-command transport is still useful information: the device appears to
contain an embedded Artosyn/Sirius Linux system, and the APK has command strings
such as:

```text
ir_cmd enable_point %d %d %d
ir_cmd enable_line %d %d %d %d %d
ir_cmd enable_rect %d %d %d %d %d
```

Those likely control on-device ROI overlays/meters, not the basic raw stream.

## Device Command Surface

`27_list_device_commands.py` statically scans `libUVCCamera_dr.so`; it does not
send anything to the camera.

The native `sendCmd` wrapper is called from 11 sites. Commands actually passed
near those call sites are:

| Function family | Command format |
|-----------------|----------------|
| point ROI delete | `ir_cmd disable_point %d` |
| point ROI enable | `ir_cmd enable_point %d %d %d` |
| line ROI delete | `ir_cmd disable_line %d` |
| line ROI enable | `ir_cmd enable_line %d %d %d %d %d` |
| rectangle ROI delete | `ir_cmd disable_rect %d` |
| rectangle ROI enable | `ir_cmd enable_rect %d %d %d %d %d` |
| firmware update step 1 | `cp /tmp/artosyn-upgrade-ars31.img /storage/artosyn-upgrade-ars31.img` |
| firmware update step 2 | `sync` |
| firmware update step 3 | `touch /usrdata/sirius-clean-system-flag` |
| firmware update step 4 | `sync` |
| firmware update step 5 | `reboot` |

No generic read-only shell commands such as `id`, `uname`, `ls`, or `cat` are
embedded in the APK. The transport may still accept arbitrary command strings,
but that is not proven yet. Any live probe should start with a harmless command
such as `echo REVASRI_PROBE` or `uname -a`, never with the firmware-update
sequence above.

`28_usb_shell_probe.py` reconstructs the native `ucmd` packet format and is
guarded by default:

```bash
python linux_port/28_usb_shell_probe.py "echo REVASRI_PROBE"        # dry-run
python linux_port/28_usb_shell_probe.py --send "echo REVASRI_PROBE" # live probe
```

It refuses dangerous commands such as `reboot` unless the script is edited or
run with explicit bypass flags. Use the live mode only when the camera is
plugged in and no capture script is using it.

## Current Python Tools

Useful tools now:

| Script | Use |
|--------|-----|
| `07_capture_frames.py` | raw frame capture from `0x82` |
| `08_view_frame.py` | diagnostic single-frame viewer for the 196-row body |
| `09_capture_stream.py` | continuous stream capture |
| `10_analyze_stimulus.py` | autocorrelation + response heatmap |
| `12_nuc_diff.py` | validates scene signal using hot/cold stimulus |
| `15_best_face.py` | picks a high-contrast NUC-subtracted stimulus frame |
| `16_calibrate_nuc.py` | captures a software NUC reference |
| `17_live_view.py` | experimental live view with software NUC |
| `21_extract_jni_table.py` | recovered native method table |
| `22_decompile_jni_targets.sh` | decompiled key JNI functions |
| `23_decompile_native_patterns.sh` | decompiled `ALCall`, `UVCPreviewIR`, and helpers |
| `25_phase_probe.py` | found that saved `stim_face.bin` is better aligned around byte offset `414` (near ties: `436`, `438`) |
| `26_row_header_probe.py` | tested the native `row+6` flag / `row+12` payload hypothesis |
| `29_frida_trace.py` | Frida USB tracer for the running Android app (hooks `ioctl`, libusb, libuvc, `executeCmd`) — needs root + frida-server on phone |
| `30_cold_init_guide.sh` | guided cold-start capture wizard (spawn-gates Frida, walks through unplug-replug) |
| `31_decode_frida_chunks.py` | decodes the 12-byte chunk headers from a Frida JSONL trace; histograms start_row / flag / stream_id |

`16_calibrate_nuc.py` and `17_live_view.py` are approximations. They do not yet
replicate the vendor `ALCall` correction pipeline, so periodic noise/artifacts
are expected.

## Row-Header Probe Result

Native `ALCall::processNUCdata` checks each row-like record at byte `+6`:

```c
if (row[6] == 0x01) {
    memcpy(dst, row + 0x0c, row_stride - 0x0c);
}
```

`26_row_header_probe.py` tested that structure against direct-bulk
`stim_face.bin` at the phase-probe offset `414`, excluding the visually bad
frames `47:90`.

Result: direct EP `0x82` data does **not** show a strong `0x01` / `0x02` flag at
`row+6` when scanned at row stride.

**Resolution (Frida trace, 20260518):** the 12-byte header IS on the wire, and
byte 6 IS `0x01` — but the header lives at *bulk-transfer* stride (every
14 348 B), not at row stride (every 524 B). The probe scanned at the wrong
period.  See "Wire-Level Chunk Format" above.  Each bulk transfer carries 28
rows; only the first 12 B of each transfer carry the row-group header.

## Next Technical Questions

- How does the native preview thread packetize frames before calling `ALCall`?
  *(Partially answered: 7 x 14 348 B bulk transfers, headers at offset 0 of
  each, see "Wire-Level Chunk Format".)*
- Can we reproduce `ALCall::getNUCdata` / `processNUCdata` in Python closely
  enough to remove the periodic noise and stripe pattern?
- ~~Are the 84 extra bytes from direct bulk capture real per-frame payload, or~~
  ~~an artifact of reading below the libuvc layer?~~  **Answered:** real wire
  payload — seven 12-byte chunk headers, one per bulk transfer.
- Can the native `ALCall` code be called directly from Linux, or is a clean-room
  Python reimplementation faster?
- What do the extracted 128-byte parameter blocks represent?
- What do the `0c XX XX XX` counter bytes (header[0..3]) and the per-stream
  byte 4-5 ID encode?  Likely a libuvc / vendor frame counter; not blocking.
