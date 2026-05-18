#!/usr/bin/env bash
# Guided cold-init capture of the REVASRI thermal-camera USB init sequence.
# Walks the user through: force-stop app -> unplug camera -> arm Frida
# spawn-gating -> replug camera -> Android launches the app -> we catch the
# very first ioctl and dump cmd1..cmd5.

set -e

PKG="com.inreii.neutralapp"
HERE="$(cd "$(dirname "$0")" && pwd)"

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
yel()   { printf "\033[33m%s\033[0m\n" "$*"; }

pause() {
  read -r -p "$(yel "  → $1") "
}

echo
bold "=== REVASRI thermal camera: cold-init capture wizard ==="
echo

# ---------------------------------------------------------------------------
bold "[1/5] Checking prerequisites"
# ---------------------------------------------------------------------------
if ! command -v adb >/dev/null; then
  red "  adb not in PATH"; exit 1
fi
if ! adb devices | awk 'NR>1 && $2=="device"{found=1} END{exit !found}'; then
  red "  No authorized adb device. Is the phone paired over wifi?"
  echo "  Try:  adb devices"
  exit 1
fi
if ! adb shell "pm list packages" 2>/dev/null | grep -q "package:$PKG"; then
  red "  Package $PKG not installed on the phone."
  exit 1
fi

echo "  Checking frida-server on phone..."
if ! adb shell 'su -c "pgrep -x frida-server"' 2>/dev/null | grep -qE '^[0-9]+$'; then
  yel "  frida-server not running — starting it..."
  adb shell 'su -c "nohup /data/local/tmp/frida-server > /data/local/tmp/frida.log 2>&1 &"' >/dev/null
  sleep 2
  if ! adb shell 'su -c "pgrep -x frida-server"' 2>/dev/null | grep -qE '^[0-9]+$'; then
    red "  failed to start frida-server. Check /data/local/tmp/frida-server exists and is +x."
    exit 1
  fi
fi
if ! frida-ps -U >/dev/null 2>&1; then
  red "  Host can't talk to frida-server over adb."
  exit 1
fi
green "  OK — adb, app, frida-server all good."
echo

# ---------------------------------------------------------------------------
bold "[2/5] Force-stopping the app for a clean launch"
# ---------------------------------------------------------------------------
adb shell "am force-stop $PKG"
sleep 0.5
green "  OK — app fully stopped."
echo

# ---------------------------------------------------------------------------
bold "[3/5] Unplug the camera from the phone"
# ---------------------------------------------------------------------------
echo "  The kernel keeps the USB endpoint state cached as long as the camera"
echo "  remains attached. Physically pulling the cable forces a full reset."
echo
pause "Unplug the camera / hub from the phone NOW. Press Enter when done."
echo

# Sanity: confirm device went away
if adb shell 'su -c "lsusb 2>/dev/null | grep -i 04b4:000a"' 2>/dev/null | grep -q 04b4:000a; then
  yel "  HMM — phone still sees the camera (04b4:000a). Unplug it for real, then press Enter again."
  pause "Press Enter once it's truly unplugged..."
fi
green "  OK — camera detached from the phone."
echo

# ---------------------------------------------------------------------------
bold "[4/5] Arming Frida spawn-gating"
# ---------------------------------------------------------------------------
echo "  About to start the trace. It will:"
echo "    1. enable spawn gating (every new process is held briefly)"
echo "    2. wait for $PKG to be launched"
echo "    3. install USB ioctl hooks BEFORE the app runs onCreate"
echo "    4. resume the app — cmd1..cmd5 fly through into our JSONL"
echo
echo "  Once you see:    spawn gating ON — waiting for an external launch ..."
echo "  THEN: plug the camera back in. Android will:"
echo "    - auto-launch the app if you previously checked 'Always open with Camera+', OR"
echo "    - show a chooser dialog — tap 'Camera+' (NOT a different app)."
echo
echo "  After the catch the trace will run for 60s. Just leave the phone alone."
echo
pause "Press Enter to start the trace..."
echo

python3 "$HERE/29_frida_trace.py" --cold-init --duration 60

echo
# ---------------------------------------------------------------------------
bold "[5/5] Summary"
# ---------------------------------------------------------------------------
LATEST=$(ls -t "$HERE/captures/frida_trace_"*.jsonl 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
  red "  No trace file found in $HERE/captures/."
  exit 1
fi
echo "  Latest trace: $LATEST"
echo
echo "  Event counts:"
python3 -c "
import json, sys
counts = {}
with open('$LATEST') as f:
    for line in f:
        try: d = json.loads(line)
        except: continue
        k = d.get('kind','?')
        counts[k] = counts.get(k, 0) + 1
for k,v in sorted(counts.items(), key=lambda x:-x[1]):
    print(f'    {v:>6}  {k}')
"
echo
INIT_HITS=$(python3 -c "
import json
n=0
with open('$LATEST') as f:
    for line in f:
        try: d=json.loads(line)
        except: continue
        if d.get('kind') in ('highlevel','usbfs_ctrl_out','uvc_call','usbfs_bulk_out'): n+=1
print(n)
")
if [ "$INIT_HITS" -gt 5 ]; then
  green "  Looks like we captured init traffic ($INIT_HITS likely-init events)."
else
  yel "  Only $INIT_HITS likely-init events captured."
  yel "  If Android didn't auto-launch the app on plug-in, the spawn-gate never fired."
  yel "  Try again and tap the app icon manually after replugging."
fi
echo
green "Done."
