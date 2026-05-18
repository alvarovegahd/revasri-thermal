#!/usr/bin/env bash
# Reverts the LXC config edits added by enable_waydroid_usb.sh and
# brings the container back up cleanly.
set -euo pipefail

CONFIG_BASE=/usr/lib/waydroid/data/configs/config_base
CONFIG_LIVE=/var/lib/waydroid/lxc/waydroid/config

if [[ $EUID -ne 0 ]]; then
    exec sudo -E bash "$0" "$@"
fi

strip_block() {
    local f=$1
    [[ -f $f ]] || return 0
    sed -i \
        -e '/# >>> revasri-thermal USB passthrough/,/# <<< revasri-thermal USB passthrough/d' \
        -e '/# USB passthrough (revasri-thermal)/d' \
        -e '/# USB passthrough$/d' \
        -e '\|^lxc\.mount\.entry = /dev/bus/usb dev/bus/usb|d' \
        -e '/^lxc\.cgroup2\.devices\.allow = c 189/d' \
        "$f"
    # Trim trailing blank lines.
    sed -i -e :a -e '/^$/{$d;N;ba' -e '}' "$f"
}

echo "[1/3] removing USB-passthrough lines..."
strip_block "$CONFIG_BASE"
strip_block "$CONFIG_LIVE"

echo "[2/3] restarting waydroid stack..."
USER_NAME=${SUDO_USER:-$(logname 2>/dev/null || echo alvaro)}
sudo -u "$USER_NAME" waydroid session stop 2>/dev/null || true
waydroid container stop 2>/dev/null || true
systemctl restart waydroid-container
sleep 2

echo "[3/3] starting fresh session as $USER_NAME..."
sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="/run/user/$(id -u "$USER_NAME")" \
    WAYLAND_DISPLAY=wayland-0 \
    nohup waydroid session start >/tmp/waydroid_session.log 2>&1 &

for i in {1..60}; do
    state=$(sudo -u "$USER_NAME" waydroid status 2>/dev/null | awk -F'\t' '/Container:/{print $2}' | tr -d '[:space:]')
    if [[ "$state" == "RUNNING" || "$state" == "FROZEN" ]]; then
        echo "    container back up after ${i}s (state=$state)"
        exit 0
    fi
    sleep 1
done

echo "    container did not come back up in 60s — check /tmp/waydroid_session.log"
exit 1
