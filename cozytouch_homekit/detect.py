"""Détection automatique des capacités exposables à HomeKit.

À partir des devices Overkiz du compte, on reconnaît les states qui mappent
proprement vers un type d'accessoire HomeKit (capteur de température, capteur
d'humidité, consigne lisible…). `configure` présente ensuite cette liste à
cocher : l'utilisateur choisit ce qu'il veut exposer, sans rien coder.

Ajouter un type = ajouter un détecteur ici (et le rendu correspondant dans
accessory.py). Les commandes (écriture : thermostat, boost ECS…) viendront
s'ajouter sur le même principe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Types d'accessoires HomeKit pris en charge (rendu dans accessory.py).
TYPE_TEMPERATURE = "temperature_sensor"
TYPE_TEMPERATURE_SETPOINT = "temperature_setpoint"  # consigne, lecture seule
TYPE_HUMIDITY = "humidity_sensor"


@dataclass
class Capability:
    """Une capacité détectée, candidate à l'exposition HomeKit."""

    key: str          # identifiant stable : "<device_url>::<state>"
    type: str         # un des TYPE_* ci-dessus
    name: str         # libellé proposé (renommable ensuite dans Maison)
    category: str     # groupe lisible pour l'affichage
    device_url: str
    state: str
    value: Any        # valeur courante (aide à identifier la sonde)


# ── Heuristiques de reconnaissance des states ────────────────────────────────
_SETPOINT_HINTS = ("target", "setpoint", "consigne")


def _is_temp_measure(name: str) -> bool:
    n = name.lower()
    return "temperature" in n and not any(h in n for h in _SETPOINT_HINTS)


def _is_temp_setpoint(name: str) -> bool:
    n = name.lower()
    return "temperature" in n and any(h in n for h in _SETPOINT_HINTS)


def _is_humidity(name: str) -> bool:
    return "humidity" in name.lower()


def _friendly_name(label: str, cname: str, state: str) -> str:
    """Libellé lisible pour la tuile (l'utilisateur pourra le renommer)."""
    c = cname.lower()
    if "outside" in c or "outdoor" in c:
        return "Température extérieure"
    if "zonetemperaturesensor" in c:
        return "Température ambiante"
    pretty = (
        state.replace("core:", "").replace("io:", "").replace("State", "").strip()
    )
    base = label or (cname.split(":")[-1] if cname else "Capteur")
    return f"{base} — {pretty}"


def detect_capabilities(devices: list[Any]) -> list[Capability]:
    """Parcourt les devices et renvoie la liste des capacités exposables."""
    caps: list[Capability] = []
    for dev in devices:
        device_url = str(getattr(dev, "device_url", ""))
        label = str(getattr(dev, "label", "") or "")
        cname = str(getattr(dev, "controllable_name", "") or "")
        for state in getattr(dev, "states", []) or []:
            sname = getattr(state, "name", None)
            if not sname:
                continue
            value = getattr(state, "value", None)
            key = f"{device_url}::{sname}"
            if _is_temp_measure(sname):
                caps.append(Capability(
                    key, TYPE_TEMPERATURE, _friendly_name(label, cname, sname),
                    "Température (mesure)", device_url, sname, value,
                ))
            elif _is_humidity(sname):
                caps.append(Capability(
                    key, TYPE_HUMIDITY, _friendly_name(label, cname, sname),
                    "Humidité", device_url, sname, value,
                ))
            elif _is_temp_setpoint(sname):
                caps.append(Capability(
                    key, TYPE_TEMPERATURE_SETPOINT, _friendly_name(label, cname, sname),
                    "Consigne température (lecture)", device_url, sname, value,
                ))
    # Tri stable : par catégorie puis nom, pour un affichage cohérent.
    caps.sort(key=lambda c: (c.category, c.name, c.state))
    return caps
