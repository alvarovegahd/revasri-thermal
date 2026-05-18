#!/usr/bin/env bash
# Install the udev rule so user (in plugdev) can talk to the camera without sudo.
# Idempotent: replaces the rule file if already present, reloads udev either way.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RULE_SRC="$HERE/99-revasri-thermal.rules"
RULE_DST="/etc/udev/rules.d/99-revasri-thermal.rules"

if [[ ! -f "$RULE_SRC" ]]; then
    echo "[01] missing $RULE_SRC" >&2
    exit 1
fi

echo "[01] installing $RULE_DST (sudo)"
sudo install -m 0644 -o root -g root "$RULE_SRC" "$RULE_DST"

echo "[01] reloading udev"
sudo udevadm control --reload-rules
sudo udevadm trigger

# Confirm the rule took effect if the camera is plugged in.
DEV_PATH=$(lsusb | awk '/04b4:000a/ {printf "/dev/bus/usb/%s/%s\n", $2, substr($4, 1, length($4)-1)}')
if [[ -n "${DEV_PATH:-}" && -e "$DEV_PATH" ]]; then
    ls -l "$DEV_PATH"
    echo "[01] if group is still 'root', unplug and replug the camera once."
else
    echo "[01] camera not currently plugged in; rule will apply on next plug-in."
fi
