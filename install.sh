#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — reproducible install on Raspberry Pi OS (ARMv6, Pi Zero W)
#
# Idempotent: safe to re-run. One single command:
#     ./install.sh
#
# Steps:
#   1) system deps (apt)
#   2) Python venv
#   3) pip install -r requirements.txt (via piwheels, prebuilt wheels)
#   4) configure (Cozytouch credentials + feature selection)
#   5) systemd service (install + enable)
#   6) first start → pairing QR code
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
SERVICE_NAME="cozytouch-homekit"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${SUDO_USER:-$USER}"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '\033[36m[install]\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m[install]\033[0m %s\n' "$*"; }

# ── 0. Guards ────────────────────────────────────────────────────────────────
if [[ ! -f /etc/rpi-issue && -z "${ALLOW_NON_RPI:-}" ]]; then
  warn "This system does not look like Raspberry Pi OS."
  warn "piwheels (prebuilt ARMv6 wheels) is only active on Raspberry Pi OS."
  warn "To force anyway: ALLOW_NON_RPI=1 ./install.sh"
  exit 1
fi

# ── 1. System dependencies ───────────────────────────────────────────────────
bold "1) System dependencies (apt)"
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; fi
$SUDO apt-get update
# python3-cryptography / python3-zeroconf: shipped PREBUILT by Debian (Rust/C
#   already compiled on their build farm). Required on ARMv6 where piwheels has
#   NO wheel for cryptography (a Rust package) nor for the latest zeroconf
#   versions. We make them visible to the venv via --system-site-packages
#   (step 2) instead of recompiling on the board (Rust = hours + OOM).
$SUDO apt-get install -y \
  python3 python3-venv python3-pip python3-dev \
  build-essential libffi-dev \
  libavahi-compat-libdnssd-dev \
  python3-cryptography python3-zeroconf

# ── 2. Virtual environment ───────────────────────────────────────────────────
bold "2) Python virtual environment"
# The venv MUST see the system packages (cryptography, zeroconf) → the
# --system-site-packages flag, which can only be set when CREATING the venv.
needs_recreate=0
if [[ ! -d "${VENV_DIR}" ]]; then
  needs_recreate=1
elif ! grep -q '^include-system-site-packages = true' "${VENV_DIR}/pyvenv.cfg" 2>/dev/null; then
  warn "Existing venv without access to system packages → recreating."
  rm -rf "${VENV_DIR}"
  needs_recreate=1
fi
if [[ "${needs_recreate}" -eq 1 ]]; then
  python3 -m venv --system-site-packages "${VENV_DIR}"
  info "venv created (--system-site-packages): ${VENV_DIR}"
else
  info "venv already present (with system packages): ${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel

# ── 3. Requirements (via piwheels) ───────────────────────────────────────────
bold "3) Installing Python dependencies (piwheels)"
# On Raspberry Pi OS, piwheels is already the default index (/etc/pip.conf).
# We add it explicitly as an extra index for other cases.
PIP_EXTRA="https://www.piwheels.org/simple"
info "Installing from requirements.txt (extra-index: piwheels)…"
pip install --extra-index-url "${PIP_EXTRA}" -r "${PROJECT_DIR}/requirements.txt"

# ── 4. Configuration ─────────────────────────────────────────────────────────
bold "4) Configuration (Cozytouch credentials + feature selection)"
if [[ -f "${PROJECT_DIR}/config.yaml" ]]; then
  info "config.yaml already exists — run 'configure' to change it."
  read -r -p "Reconfigure now? [y/N] " ans
  if [[ "${ans:-N}" =~ ^[oOyY]$ ]]; then
    python -m cozytouch_homekit configure
  fi
else
  python -m cozytouch_homekit configure
fi

# ── 4b. File ownership ───────────────────────────────────────────────────────
# install.sh runs under sudo (root); the systemd service runs as RUN_USER.
# Everything root created (venv, config.yaml) must belong to RUN_USER, otherwise
# the service can neither read config.yaml (0600 root) nor write accessory.state
# in the directory.
if [[ "${RUN_USER}" != "root" ]]; then
  $SUDO chown -R "${RUN_USER}:${RUN_USER}" "${VENV_DIR}" 2>/dev/null || true
  [[ -f "${PROJECT_DIR}/config.yaml" ]] && \
    $SUDO chown "${RUN_USER}:${RUN_USER}" "${PROJECT_DIR}/config.yaml"
  # The project dir must be writable by RUN_USER (accessory.state).
  $SUDO chown "${RUN_USER}:${RUN_USER}" "${PROJECT_DIR}" 2>/dev/null || true
  info "Artifact ownership assigned to ${RUN_USER}."
fi

# ── 5. systemd service ───────────────────────────────────────────────────────
bold "5) systemd service"
TMP_UNIT="$(mktemp)"
sed -e "s|__USER__|${RUN_USER}|g" \
    -e "s|__WORKDIR__|${PROJECT_DIR}|g" \
    "${PROJECT_DIR}/systemd/${SERVICE_NAME}.service" > "${TMP_UNIT}"
$SUDO cp "${TMP_UNIT}" "${SERVICE_FILE}"
rm -f "${TMP_UNIT}"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "${SERVICE_NAME}"
info "Service installed and enabled: ${SERVICE_FILE}"

# ── 6. First start / QR code ─────────────────────────────────────────────────
bold "6) Pairing"
echo
info "To pair, start the service and watch the QR code in the logs:"
echo "    sudo systemctl restart ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo
warn "ARMv6 (Pi Zero): pairing can take several minutes. Be patient."
echo
read -r -p "Start the service and show the logs now? [Y/n] " go
if [[ ! "${go:-Y}" =~ ^[nN]$ ]]; then
  $SUDO systemctl restart "${SERVICE_NAME}"
  exec $SUDO journalctl -u "${SERVICE_NAME}" -f
fi
bold "Installation complete."
