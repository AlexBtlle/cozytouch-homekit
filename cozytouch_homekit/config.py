"""Chargement / sauvegarde de la configuration (config.yaml)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Racine du projet (dossier parent du package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("COZYTOUCH_CONFIG", PROJECT_ROOT / "config.yaml"))

# Liste canonique des features, dans un ORDRE STABLE.
# Cet ordre détermine l'ordre d'ajout des services HAP donc la stabilité
# des IID entre redémarrages : ne jamais le réordonner.
FEATURE_ORDER = [
    "temp_ambiante",
    "temp_exterieure",
    "temp_ecs",
    "thermostat",
    "boost_ecs",
]

# Features réellement implémentées en V1 (lecture seule).
IMPLEMENTED_FEATURES = {"temp_ambiante", "temp_exterieure", "temp_ecs"}

DEFAULT_CONFIG: dict[str, Any] = {
    # Langue de l'interface (page web) : "en" ou "fr". / UI language.
    "language": "en",
    "cozytouch": {
        "username": "",
        "password": "",
        "server": "atlantic_cozytouch",
    },
    "device": {
        "pac_url": "",
        "ecs_url": "",
    },
    "sensors": {
        # Chaque capteur cible son PROPRE device_url (les sondes ambiante /
        # extérieure / ECS sont des sous-devices Overkiz distincts). À remplir
        # via `configure` ou après `explore`. `device` (pac/ecs) reste accepté
        # en repli legacy si device_url est vide.
        "temp_ambiante": {"device_url": "", "state": "core:TemperatureState"},
        "temp_exterieure": {"device_url": "", "state": "core:TemperatureState"},
        "temp_ecs": {"device_url": "", "state": "core:TargetDHWTemperatureState"},
    },
    "features": {
        "temp_ambiante": True,
        "temp_exterieure": True,
        "temp_ecs": False,
        "thermostat": False,
        "boost_ecs": False,
    },
    "homekit": {
        "name": "PAC Atlantic",
        "port": 51826,
        "persist_file": "accessory.state",
    },
    "polling": {
        "interval": 120,
        "backoff_base": 30,
        "backoff_max": 600,
    },
    # Mini page web de statut (port 8080 par défaut). enabled: false pour la
    # désactiver. ⚠️ affiche le PIN d'appairage + infos système (LAN de confiance).
    "web": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8080,
    },
    # Liste des capacités exposées à HomeKit, détectées et choisies via
    # `configure` (un accessoire par entrée). Chaque entrée :
    #   {aid, type, name, device_url, state}
    # Si vide, le bridge retombe sur le mode legacy (features/sensors).
    "exposed": [],
}


def assign_aids(exposed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Garantit un AID stable et unique (>= 2) par entrée exposée.

    Conserve les AID déjà attribués ; les nouvelles entrées reçoivent le plus
    petit AID libre. Mute et renvoie la liste.
    """
    used = {e["aid"] for e in exposed if isinstance(e.get("aid"), int)}
    next_aid = 2
    for entry in exposed:
        if not isinstance(entry.get("aid"), int):
            while next_aid in used:
                next_aid += 1
            entry["aid"] = next_aid
            used.add(next_aid)
    return exposed


def _deep_merge(base: dict, override: dict) -> dict:
    """Fusion récursive : `override` complète/écrase `base` sans le muter."""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Charge config.yaml fusionné sur les défauts. Erreur si absent."""
    path = path or CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration introuvable : {path}\n"
            "Lancez d'abord :  python -m cozytouch_homekit configure"
        )
    with path.open("r", encoding="utf-8") as fh:
        user_cfg = yaml.safe_load(fh) or {}
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    """Écrit config.yaml (droits 0600, contient des secrets)."""
    path = path or CONFIG_PATH
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
    try:
        path.chmod(0o600)
    except OSError:
        pass  # systèmes de fichiers sans support des permissions POSIX
    return path


def resolve_persist_path(cfg: dict[str, Any]) -> Path:
    """Chemin absolu du fichier d'état HAP."""
    persist = cfg["homekit"]["persist_file"]
    p = Path(persist)
    return p if p.is_absolute() else PROJECT_ROOT / p


def enabled_features(cfg: dict[str, Any]) -> list[str]:
    """Features activées ET implémentées, dans l'ordre canonique stable."""
    feats = cfg.get("features", {})
    return [
        name
        for name in FEATURE_ORDER
        if feats.get(name) and name in IMPLEMENTED_FEATURES
    ]
