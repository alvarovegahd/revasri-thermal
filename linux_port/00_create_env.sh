#!/usr/bin/env bash
# Create the `thermal-cam` conda env with everything the pipeline needs.
# Idempotent: if the env already exists, just makes sure deps are installed.
# See 04_install_jadx.sh — conda activate hooks can reference unbound vars.
set -eo pipefail

ENV_NAME="thermal-cam"
PY_VER="3.11"
CONDA_DEPS=(numpy matplotlib pillow)
PIP_DEPS=(pyusb)

# shellcheck disable=SC1091
source "$(conda info --base)/etc/profile.d/conda.sh"

if conda env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
    echo "[00] env '$ENV_NAME' already exists — skipping create"
else
    echo "[00] creating env '$ENV_NAME' (python=$PY_VER, ${CONDA_DEPS[*]})"
    conda create -n "$ENV_NAME" -y "python=$PY_VER" "${CONDA_DEPS[@]}"
fi

conda activate "$ENV_NAME"

echo "[00] installing pip deps: ${PIP_DEPS[*]}"
pip install --quiet --upgrade "${PIP_DEPS[@]}"

echo "[00] verifying imports"
python -c "import usb.core, usb.util, numpy, matplotlib; print('  imports OK')"

echo "[00] done. Activate with: conda activate $ENV_NAME"
