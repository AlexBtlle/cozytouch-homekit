"""Chiffrement au repos du mot de passe Cozytouch.

Le mot de passe n'est plus stocké en clair dans config.yaml : il y figure
chiffré (`enc:v1:<token Fernet>`). La clé de chiffrement vit dans un fichier
séparé `.secret.key` (0600, gitignoré), à côté de config.yaml.

Modèle de menace (honnête) : ceci empêche la lecture à l'œil et le partage
accidentel de config.yaml d'exposer le mot de passe. Ce n'est PAS un coffre-fort
— un accès root à la machine (donc aux deux fichiers) permet de déchiffrer.
Pour un durcissement OS, voir systemd-creds.
"""

from __future__ import annotations

import logging

from .config import CONFIG_PATH

_LOGGER = logging.getLogger(__name__)

ENC_PREFIX = "enc:v1:"
KEY_PATH = CONFIG_PATH.parent / ".secret.key"


def _get_or_create_key() -> bytes:
    from cryptography.fernet import Fernet

    if KEY_PATH.exists():
        return KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    try:
        KEY_PATH.chmod(0o600)
    except OSError:
        pass
    return key


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt(plaintext: str) -> str:
    """Chiffre un mot de passe → 'enc:v1:<token>'."""
    from cryptography.fernet import Fernet

    token = Fernet(_get_or_create_key()).encrypt(plaintext.encode()).decode()
    return ENC_PREFIX + token


def decrypt(value: str) -> str:
    """Déchiffre une valeur 'enc:v1:…'. Renvoie tel quel si non chiffrée (legacy)."""
    if not is_encrypted(value):
        return value
    from cryptography.fernet import Fernet

    try:
        return Fernet(_get_or_create_key()).decrypt(
            value[len(ENC_PREFIX):].encode()
        ).decode()
    except Exception as exc:  # noqa: BLE001 — InvalidToken, clé absente/modifiée…
        _LOGGER.error(
            "Déchiffrement du mot de passe impossible (%s) — clé .secret.key "
            "absente ou modifiée ? Relancez `configure`.", exc,
        )
        return ""
