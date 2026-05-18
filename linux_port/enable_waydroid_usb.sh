#!/usr/bin/env bash
# Enables USB passthrough into the waydroid container.
# - Removes any previous USB-passthrough block we added
# - Adds a minimal bind-mount-only variant (no cgroup line, which broke boot)
# - Restarts the container
#
# Run with: sudo bash enable_waydroid_usb.sh
set -euo pipefail

CONFIG_BASE=/usr/lib/waydroid/data/configs/config_base
CONFIG_LIVE=/var/lib/waydroid/lxc/waydroid/config
MARKER_BEGIN='# >>> revasri-thermal USB passthrough'
MARKER_END='# <<< revasri-thermal USB passthrough'

if [[ $EUID -ne 0 ]]; then
    echo "Re-running with sudo..."
    exec sudo -E bash "$0" "$@"
fi

USER_NAME=${SUDO_USER:-$(logname 2>/dev/null || echo alvaro)}

strip_block() {
    local f=$1
    # Remove our begin..end markers AND any stray lines from the previous script.
    sed -i \
        -e "/$MARKER_BEGIN/,/$MARKER_END/d" \
        -e '/# USB passthrough (revasri-thermal)/d' \
        -e '/# USB passthrough$/d' \
        -e '\|^lxc\.mount\.entry = /dev/bus/usb dev/bus/usb|d' \
        -e '/^lxc\.cgroup2\.devices\.allow = c 189/d' \
        "$f"
}

append_block() {
    local f=$1
    cat >> "$f" <<EOF

$MARKER_BEGIN
lxc.mount.entry = /dev/bus/usb dev/bus/usb none bind,optional,create=dir 0 0
$MARKER_END
EOF
}

echo "[1/4] cleaning previous USB-passthrough additions..."
strip_block "$CONFIG_BASE"
strip_block "$CONFIG_LIVE"

echo "[2/4] adding bind-mount-only block..."
append_block "$CONFIG_BASE"
append_block "$CONFIG_LIVE"

echo "[3/4] stopping then starting container..."
waydroid container stop 2>/dev/null || true
sleep 2
# Container start needs to run in the foreground long enough to settle.
nohup waydroid container start >/tmp/waydroid_container.log 2>&1 &
START_PID=$!

echo "    waiting (up to 60s) for container to come up..."
for i in {1..60}; do
    state=$(sudo -u "$USER_NAME" waydroid status 2>/dev/null | awk -F'\t' '/Container:/{print $2}' | tr -d '[:space:]')
    if [[ "$state" == "RUNNING" || "$state" == "FROZEN" ]]; then
        echo "    container ready after ${i}s (state=$state)"
        break
    fi
    sleep 1
done

echo "[4/4] checking USB visibility inside container..."
sleep 2
sudo -u "$USER_NAME" waydroid shell ls /dev/bus/usb/ 2>&1 || \
    echo "    (couldn't list /dev/bus/usb yet — try after container fully boots)"

echo
echo "Container start log: /tmp/waydroid_container.log"
echo "Next:"
echo "  waydroid app launch com.inreii.neutralapp"
