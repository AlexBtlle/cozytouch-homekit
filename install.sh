#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install.sh — installation reproductible sur Raspberry Pi OS (ARMv6, Pi Zero W)
#
# Idempotent : ré-exécutable sans casse. Une seule commande :
#     ./install.sh
#
# Étapes :
#   1) deps système (apt)
#   2) venv Python
#   3) pip install -r requirements.txt (via piwheels, wheels précompilées)
#   4) configure (identifiants Cozytouch + choix des fonctions)
#   5) service systemd (install + activation)
#   6) 1er démarrage → QR code d'appairage
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

# ── 0. Garde-fous ────────────────────────────────────────────────────────────
if [[ ! -f /etc/rpi-issue && -z "${ALLOW_NON_RPI:-}" ]]; then
  warn "Ce système ne semble pas être Raspberry Pi OS."
  warn "piwheels (wheels ARMv6 précompilées) n'est actif que sur Raspberry Pi OS."
  warn "Pour forcer quand même : ALLOW_NON_RPI=1 ./install.sh"
  exit 1
fi

# ── 1. Dépendances système ───────────────────────────────────────────────────
bold "1) Dépendances système (apt)"
SUDO=""
if [[ "$(id -u)" -ne 0 ]]; then SUDO="sudo"; fi
$SUDO apt-get update
# python3-cryptography / python3-zeroconf : fournis PRÉCOMPILÉS par Debian
#   (Rust/C déjà compilés côté serveur). Indispensable sur ARMv6 où piwheels
#   n'a PAS de wheel pour cryptography (paquet Rust) ni pour les dernières
#   versions de zeroconf. On les rend visibles au venv via --system-site-packages
#   (étape 2) au lieu de les recompiler sur la carte (Rust = des heures + OOM).
$SUDO apt-get install -y \
  python3 python3-venv python3-pip python3-dev \
  build-essential libffi-dev \
  libavahi-compat-libdnssd-dev \
  python3-cryptography python3-zeroconf

# ── 2. Environnement virtuel ─────────────────────────────────────────────────
bold "2) Environnement virtuel Python"
# Le venv DOIT voir les paquets système (cryptography, zeroconf) → flag
# --system-site-packages, qui ne peut être posé qu'à la CRÉATION du venv.
needs_recreate=0
if [[ ! -d "${VENV_DIR}" ]]; then
  needs_recreate=1
elif ! grep -q '^include-system-site-packages = true' "${VENV_DIR}/pyvenv.cfg" 2>/dev/null; then
  warn "venv existant sans accès aux paquets système → recréation."
  rm -rf "${VENV_DIR}"
  needs_recreate=1
fi
if [[ "${needs_recreate}" -eq 1 ]]; then
  python3 -m venv --system-site-packages "${VENV_DIR}"
  info "venv créé (--system-site-packages) : ${VENV_DIR}"
else
  info "venv déjà présent (avec paquets système) : ${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel

# ── 3. Requirements (via piwheels) ───────────────────────────────────────────
bold "3) Installation des dépendances Python (piwheels)"
# Sur Raspberry Pi OS, piwheels est déjà l'index par défaut (/etc/pip.conf).
# On l'ajoute explicitement en extra-index pour les autres cas.
PIP_EXTRA="https://www.piwheels.org/simple"
info "Installation depuis requirements.txt (extra-index : piwheels)…"
pip install --extra-index-url "${PIP_EXTRA}" -r "${PROJECT_DIR}/requirements.txt"

# ── 4. Configuration ─────────────────────────────────────────────────────────
bold "4) Configuration (identifiants Cozytouch + choix des fonctions)"
if [[ -f "${PROJECT_DIR}/config.yaml" ]]; then
  info "config.yaml existe déjà — relancez 'configure' pour le modifier."
  read -r -p "Reconfigurer maintenant ? [o/N] " ans
  if [[ "${ans:-N}" =~ ^[oOyY]$ ]]; then
    python -m cozytouch_homekit configure
  fi
else
  python -m cozytouch_homekit configure
fi

# ── 5. Service systemd ───────────────────────────────────────────────────────
bold "5) Service systemd"
TMP_UNIT="$(mktemp)"
sed -e "s|__USER__|${RUN_USER}|g" \
    -e "s|__WORKDIR__|${PROJECT_DIR}|g" \
    "${PROJECT_DIR}/systemd/${SERVICE_NAME}.service" > "${TMP_UNIT}"
$SUDO cp "${TMP_UNIT}" "${SERVICE_FILE}"
rm -f "${TMP_UNIT}"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "${SERVICE_NAME}"
info "Service installé et activé : ${SERVICE_FILE}"

# ── 6. Premier démarrage / QR code ───────────────────────────────────────────
bold "6) Appairage"
echo
info "Pour appairer, démarrez le service et regardez le QR code dans les logs :"
echo "    sudo systemctl restart ${SERVICE_NAME}"
echo "    journalctl -u ${SERVICE_NAME} -f"
echo
warn "ARMv6 (Pi Zero) : l'appairage peut prendre plusieurs minutes. Patience."
echo
read -r -p "Démarrer le service et afficher les logs maintenant ? [O/n] " go
if [[ ! "${go:-O}" =~ ^[nN]$ ]]; then
  $SUDO systemctl restart "${SERVICE_NAME}"
  exec $SUDO journalctl -u "${SERVICE_NAME}" -f
fi
bold "Installation terminée."
