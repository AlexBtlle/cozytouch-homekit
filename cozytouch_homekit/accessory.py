"""Bridge HAP pour la PAC Atlantic — un accessoire par capacité exposée.

Architecture : 1 **bridge** (`CozytouchBridge`) qui héberge N accessoires
enfants, chacun exposant UNE capacité détectée (capteur de température,
d'humidité, consigne…). Chaque accessoire ponté est **assignable à sa propre
pièce** dans l'app Maison.

Le bridge est piloté par la liste `exposed` de la config (détectée et choisie
via `configure`). À défaut, repli legacy sur `features`/`sensors`.

- AID stables : le bridge = AID 1 ; chaque enfant porte l'AID figé dans son
  entrée `exposed` (cf. config.assign_aids).
- Le polling Overkiz + backoff vivent dans `CozytouchBridge.run()` (même event
  loop que HAP-python). Le bridge lit les states une fois par device puis
  pousse les valeurs à chaque accessoire via `update()`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyhap.accessory import Accessory, Bridge
from pyhap.const import CATEGORY_SENSOR

from .config import FEATURE_ORDER, IMPLEMENTED_FEATURES
from .detect import (
    TYPE_HUMIDITY,
    TYPE_TEMPERATURE,
    TYPE_TEMPERATURE_SETPOINT,
)
from .overkiz_client import AuthError, CozytouchClient, TransientError

_LOGGER = logging.getLogger(__name__)

# Libellés legacy (mode features/sensors).
SENSOR_NAMES = {
    "temp_ambiante": "Température ambiante",
    "temp_exterieure": "Température extérieure",
    "temp_ecs": "Température ECS",
}

TEMP_MIN = -50.0
TEMP_MAX = 100.0

FAULT_NONE = 0
FAULT_GENERAL = 1


def feature_aid(feature: str) -> int:
    """AID legacy déterministe pour une feature (>= 2)."""
    return 2 + FEATURE_ORDER.index(feature)


class _SensorAccessory(Accessory):
    """Base : un accessoire enfant lisant un (device_url, state) Overkiz."""

    category = CATEGORY_SENSOR
    kind = "sensor"
    unit = ""

    def __init__(self, driver, name: str, device_url: str, state_name: str, aid: int):
        super().__init__(driver, name, aid=aid)
        self.device_url = device_url
        self.state_name = state_name
        self._name = name
        self._warned_missing = False

        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="Atlantic")
        info.configure_char("Model", value="Cozytouch")
        info.configure_char("SerialNumber", value=f"cozytouch-{aid}")
        info.configure_char("FirmwareRevision", value="1.0.0")

    @property
    def device_urls(self) -> list[str]:
        return [self.device_url]

    # À implémenter par les sous-classes.
    def _apply(self, value: Any) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def set_unavailable(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def current_value(self) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        """Instantané pour la page web."""
        return {
            "name": self._name,
            "kind": self.kind,
            "unit": self.unit,
            "value": self.current_value(),
            "available": bool(self.char_active.value),
            "device_url": self.device_url,
            "state": self.state_name,
        }

    def update(self, states_by_url: dict[str, dict[str, Any]]) -> None:
        states = states_by_url.get(self.device_url, {})
        value = states.get(self.state_name)
        if value is None:
            if not self._warned_missing:
                avail = sorted(states)
                _LOGGER.warning(
                    "State '%s' introuvable sur %s (accessoire '%s'). "
                    "States disponibles : %s.",
                    self.state_name, self.device_url, self._name,
                    ", ".join(avail) or "(aucun)",
                )
                self._warned_missing = True
            self.set_unavailable()
            return
        self._warned_missing = False
        try:
            self._apply(value)
        except (TypeError, ValueError):
            _LOGGER.warning("Valeur inattendue pour %s : %r", self._name, value)
            self.set_unavailable()


class TemperatureSensorAccessory(_SensorAccessory):
    kind = "température"
    unit = "°C"

    def __init__(self, driver, name, device_url, state_name, aid):
        super().__init__(driver, name, device_url, state_name, aid)
        svc = self.add_preload_service(
            "TemperatureSensor", chars=["StatusActive", "StatusFault"]
        )
        self.char_temp = svc.get_characteristic("CurrentTemperature")
        self.char_temp.override_properties(
            properties={"minValue": TEMP_MIN, "maxValue": TEMP_MAX, "minStep": 0.1}
        )
        self.char_active = svc.get_characteristic("StatusActive")
        self.char_fault = svc.get_characteristic("StatusFault")
        self.set_unavailable()

    def _apply(self, value: Any) -> None:
        clamped = max(TEMP_MIN, min(TEMP_MAX, float(value)))
        self.char_temp.set_value(clamped)
        self.char_active.set_value(True)
        self.char_fault.set_value(FAULT_NONE)

    def set_unavailable(self) -> None:
        self.char_active.set_value(False)
        self.char_fault.set_value(FAULT_GENERAL)

    def current_value(self) -> Any:
        return self.char_temp.value


class HumiditySensorAccessory(_SensorAccessory):
    kind = "humidité"
    unit = "%"

    def __init__(self, driver, name, device_url, state_name, aid):
        super().__init__(driver, name, device_url, state_name, aid)
        svc = self.add_preload_service(
            "HumiditySensor", chars=["StatusActive", "StatusFault"]
        )
        self.char_hum = svc.get_characteristic("CurrentRelativeHumidity")
        self.char_active = svc.get_characteristic("StatusActive")
        self.char_fault = svc.get_characteristic("StatusFault")
        self.set_unavailable()

    def _apply(self, value: Any) -> None:
        clamped = max(0.0, min(100.0, float(value)))
        self.char_hum.set_value(clamped)
        self.char_active.set_value(True)
        self.char_fault.set_value(FAULT_NONE)

    def set_unavailable(self) -> None:
        self.char_active.set_value(False)
        self.char_fault.set_value(FAULT_GENERAL)

    def current_value(self) -> Any:
        return self.char_hum.value


# type d'entrée `exposed` → classe d'accessoire.
_ACCESSORY_BY_TYPE = {
    TYPE_TEMPERATURE: TemperatureSensorAccessory,
    TYPE_TEMPERATURE_SETPOINT: TemperatureSensorAccessory,
    TYPE_HUMIDITY: HumiditySensorAccessory,
}


class CozytouchBridge(Bridge):
    """Bridge HAP : un accessoire par capacité exposée."""

    def __init__(self, driver, cfg: dict[str, Any]):
        super().__init__(driver, cfg["homekit"]["name"])
        self._cfg = cfg
        self._client: CozytouchClient | None = None
        self._components: list[_SensorAccessory] = []
        self._stopped = False
        self._run_task: asyncio.Task | None = None
        self._web: Any = None
        # État exposé à la page web de statut.
        self.connected = False
        self.last_poll_ok: Any = None
        self.last_error: str | None = None

        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="Atlantic")
        info.configure_char("Model", value="Cozytouch")
        info.configure_char(
            "SerialNumber", value=self._cfg["device"].get("pac_url") or "cozytouch-bridge"
        )

        self._build()

    # ── Construction ─────────────────────────────────────────────────────────
    def _legacy_exposed(self) -> list[dict[str, Any]]:
        """Convertit l'ancien schéma features/sensors en entrées `exposed`."""
        features = self._cfg.get("features", {})
        sensors = self._cfg.get("sensors", {})
        out: list[dict[str, Any]] = []
        for feature in FEATURE_ORDER:
            if not features.get(feature) or feature not in IMPLEMENTED_FEATURES:
                continue
            mapping = sensors.get(feature, {})
            device_url = mapping.get("device_url") or (
                self._cfg["device"].get("ecs_url" if mapping.get("device") == "ecs" else "pac_url", "")
            )
            state = mapping.get("state", "")
            if device_url and state:
                out.append({
                    "aid": feature_aid(feature),
                    "type": TYPE_TEMPERATURE,
                    "name": SENSOR_NAMES.get(feature, feature),
                    "device_url": device_url,
                    "state": state,
                })
        return out

    def _build(self) -> None:
        exposed = self._cfg.get("exposed") or self._legacy_exposed()
        for entry in exposed:
            acc_cls = _ACCESSORY_BY_TYPE.get(entry.get("type"))
            if acc_cls is None:
                _LOGGER.warning("Type d'accessoire inconnu, ignoré : %r", entry.get("type"))
                continue
            device_url = entry.get("device_url", "")
            state = entry.get("state", "")
            aid = entry.get("aid")
            if not device_url or not state or not isinstance(aid, int):
                _LOGGER.error("Entrée exposée invalide (ignorée) : %r", entry)
                continue
            acc = acc_cls(self.driver, entry.get("name", state), device_url, state, aid)
            self.add_accessory(acc)
            self._components.append(acc)
            _LOGGER.info(
                "Accessoire '%s' (AID %s, %s) ← %s @ %s",
                entry.get("name"), aid, entry.get("type"), state, device_url,
            )
        if not self._components:
            _LOGGER.warning(
                "Aucun accessoire exposé. Lancez `configure` pour en choisir."
            )

    @property
    def _device_urls(self) -> list[str]:
        urls: list[str] = []
        for comp in self._components:
            for url in comp.device_urls:
                if url not in urls:
                    urls.append(url)
        return urls

    # ── Boucle de fonctionnement ─────────────────────────────────────────────
    async def run(self) -> None:
        cz = self._cfg["cozytouch"]
        poll = self._cfg["polling"]
        interval = int(poll["interval"])
        backoff_base = int(poll["backoff_base"])
        backoff_max = int(poll["backoff_max"])

        self._client = CozytouchClient(cz["username"], cz["password"], cz["server"])
        backoff = backoff_base
        self._run_task = asyncio.current_task()

        await self._start_web()

        while not self._stopped:
            try:
                await self._poll_once()
                self.connected = True
                self.last_error = None
                from datetime import datetime

                self.last_poll_ok = datetime.now()
                backoff = backoff_base
                await self._sleep(interval)
            except AuthError as exc:
                _LOGGER.error("%s — corrigez `configure`. Pause prolongée.", exc)
                self.connected = False
                self.last_error = str(exc)
                self._mark_all_unavailable()
                await self._sleep(backoff_max)
            except TransientError as exc:
                _LOGGER.warning("Erreur temporaire : %s. Backoff %ss.", exc, backoff)
                self.connected = False
                self.last_error = str(exc)
                self._mark_all_unavailable()
                await self._sleep(backoff)
                backoff = min(backoff_max, backoff * 2)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                _LOGGER.exception("Erreur inattendue dans la boucle : %s", exc)
                self.connected = False
                self.last_error = str(exc)
                self._mark_all_unavailable()
                await self._sleep(backoff)
                backoff = min(backoff_max, backoff * 2)

    async def _poll_once(self) -> None:
        assert self._client is not None
        states_by_url: dict[str, dict[str, Any]] = {}
        for url in self._device_urls:
            states_by_url[url] = await self._client.read_states(url)
        for comp in self._components:
            comp.update(states_by_url)

    def _mark_all_unavailable(self) -> None:
        for comp in self._components:
            comp.set_unavailable()

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _start_web(self) -> None:
        """Démarre la page de statut si activée (best-effort)."""
        if not self._cfg.get("web", {}).get("enabled", False):
            return
        try:
            from .webserver import StatusServer

            self._web = StatusServer(self, self._cfg)
            await self._web.start()
        except Exception as exc:  # noqa: BLE001 — la page web ne doit pas tuer le service
            _LOGGER.warning("Page de statut non démarrée : %s", exc)
            self._web = None

    async def stop(self) -> None:
        self._stopped = True
        if self._web is not None:
            await self._web.stop()
            self._web = None
        task = self._run_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._client is not None:
            await self._client.close()
        await super().stop()
