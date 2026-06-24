"""Bridge HAP pour la PAC Atlantic — un accessoire par fonction.

Architecture : 1 **bridge** (`CozytouchBridge`) qui héberge N accessoires
enfants, chacun exposant UNE fonction (un capteur de température, plus tard un
thermostat, un boost ECS…). Contrairement à un accessoire standalone, chaque
accessoire ponté est **assignable à sa propre pièce** dans l'app Maison.

- AID stables : le bridge = AID 1 ; chaque enfant reçoit un AID déterministe
  dérivé de sa position dans `FEATURE_ORDER` (stable entre redémarrages, même
  si on active/désactive des fonctions).
- Le polling Overkiz + backoff vivent dans `CozytouchBridge.run()` (même event
  loop que HAP-python). Le bridge lit les states une fois par device puis
  pousse les valeurs à chaque accessoire enfant via `update()`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyhap.accessory import Accessory, Bridge
from pyhap.const import CATEGORY_SENSOR

from .config import FEATURE_ORDER, IMPLEMENTED_FEATURES
from .overkiz_client import AuthError, CozytouchClient, TransientError

_LOGGER = logging.getLogger(__name__)

# Libellés des tuiles HomeKit.
SENSOR_NAMES = {
    "temp_ambiante": "Température ambiante",
    "temp_exterieure": "Température extérieure",
    "temp_ecs": "Température ECS",
}

# Bornes HomeKit pour CurrentTemperature (°C). Plage basse élargie pour les
# températures extérieures négatives.
TEMP_MIN = -50.0
TEMP_MAX = 100.0

# StatusFault : 0 = NO_FAULT, 1 = GENERAL_FAULT.
FAULT_NONE = 0
FAULT_GENERAL = 1


def feature_aid(feature: str) -> int:
    """AID déterministe et stable pour un accessoire enfant (>= 2)."""
    return 2 + FEATURE_ORDER.index(feature)


class TemperatureSensorAccessory(Accessory):
    """Accessoire enfant = un capteur de température (lecture seule)."""

    category = CATEGORY_SENSOR

    def __init__(self, driver, feature: str, device_url: str, state_name: str):
        super().__init__(driver, SENSOR_NAMES.get(feature, feature), aid=feature_aid(feature))
        self.feature = feature
        self.device_url = device_url
        self.state_name = state_name
        self._warned_missing = False

        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="Atlantic")
        info.configure_char("Model", value="Alfea Extensa AI Duo")
        info.configure_char("SerialNumber", value=f"cozytouch-{feature}")
        info.configure_char("FirmwareRevision", value="1.0.0")

        svc = self.add_preload_service(
            "TemperatureSensor", chars=["StatusActive", "StatusFault"]
        )
        self.char_temp = svc.get_characteristic("CurrentTemperature")
        self.char_temp.override_properties(
            properties={"minValue": TEMP_MIN, "maxValue": TEMP_MAX, "minStep": 0.1}
        )
        self.char_fault = svc.get_characteristic("StatusFault")
        self.char_active = svc.get_characteristic("StatusActive")
        self.set_unavailable()  # état initial tant qu'on n'a pas lu

    @property
    def device_urls(self) -> list[str]:
        return [self.device_url]

    def set_temperature(self, value: float) -> None:
        clamped = max(TEMP_MIN, min(TEMP_MAX, float(value)))
        self.char_temp.set_value(clamped)
        self.char_active.set_value(True)
        self.char_fault.set_value(FAULT_NONE)

    def set_unavailable(self) -> None:
        """API muette : on signale un défaut plutôt qu'une valeur figée trompeuse."""
        self.char_active.set_value(False)
        self.char_fault.set_value(FAULT_GENERAL)

    def update(self, states_by_url: dict[str, dict[str, Any]]) -> None:
        states = states_by_url.get(self.device_url, {})
        value = states.get(self.state_name)
        if value is None:
            if not self._warned_missing:
                temp_states = sorted(n for n in states if "emperatur" in n.lower())
                _LOGGER.warning(
                    "State '%s' introuvable sur %s (capteur '%s'). "
                    "States de température disponibles : %s. "
                    "Corrigez `sensors.%s.state` dans config.yaml.",
                    self.state_name, self.device_url, self.feature,
                    ", ".join(temp_states) or "(aucun)", self.feature,
                )
                self._warned_missing = True
            self.set_unavailable()
            return
        self._warned_missing = False
        try:
            self.set_temperature(float(value))
        except (TypeError, ValueError):
            _LOGGER.warning("Valeur non numérique pour %s : %r", self.feature, value)
            self.set_unavailable()


class CozytouchBridge(Bridge):
    """Bridge HAP : héberge un accessoire par fonction activée."""

    def __init__(self, driver, cfg: dict[str, Any]):
        super().__init__(driver, cfg["homekit"]["name"])
        self._cfg = cfg
        self._client: CozytouchClient | None = None
        # Accessoires enfants exposant chacun .device_urls, .update(), .set_unavailable()
        self._components: list[Any] = []
        self._stopped = False
        self._run_task: asyncio.Task | None = None

        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="Atlantic")
        info.configure_char("Model", value="Alfea Extensa AI Duo")
        info.configure_char(
            "SerialNumber", value=self._cfg["device"].get("pac_url") or "cozytouch-bridge"
        )

        self._build()

    # ── Construction ─────────────────────────────────────────────────────────
    def _resolve_url(self, device_key: str) -> str:
        """Repli legacy device: pac/ecs (si un capteur n'a pas de device_url)."""
        if device_key == "ecs":
            return self._cfg["device"].get("ecs_url", "")
        return self._cfg["device"].get("pac_url", "")

    def _build(self) -> None:
        features = self._cfg.get("features", {})
        sensors = self._cfg.get("sensors", {})

        for feature in FEATURE_ORDER:
            if not features.get(feature):
                continue
            if feature not in IMPLEMENTED_FEATURES:
                _LOGGER.warning(
                    "Feature '%s' activée mais non implémentée — ignorée.", feature
                )
                continue

            mapping = sensors.get(feature, {})
            device_url = mapping.get("device_url") or self._resolve_url(
                mapping.get("device", "pac")
            )
            state_name = mapping.get("state", "")
            if not device_url or not state_name:
                _LOGGER.error(
                    "Feature '%s' activée mais device_url/state manquant "
                    "(device_url=%r state=%r) — accessoire non créé. Lancez "
                    "`explore` puis `configure`.",
                    feature, device_url, state_name,
                )
                continue

            acc = TemperatureSensorAccessory(self.driver, feature, device_url, state_name)
            self.add_accessory(acc)
            self._components.append(acc)
            _LOGGER.info(
                "Accessoire '%s' (AID %s) ← %s @ %s",
                feature, acc.aid, state_name, device_url,
            )

        if not self._components:
            _LOGGER.warning(
                "Aucun accessoire créé. Vérifiez features + device_url + states."
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
        """Boucle de polling (lancée une fois par le driver, dans sa loop)."""
        cz = self._cfg["cozytouch"]
        poll = self._cfg["polling"]
        interval = int(poll["interval"])
        backoff_base = int(poll["backoff_base"])
        backoff_max = int(poll["backoff_max"])

        self._client = CozytouchClient(cz["username"], cz["password"], cz["server"])
        backoff = backoff_base
        self._run_task = asyncio.current_task()

        while not self._stopped:
            try:
                await self._poll_once()
                backoff = backoff_base
                await self._sleep(interval)
            except AuthError as exc:
                _LOGGER.error("%s — corrigez `configure`. Pause prolongée.", exc)
                self._mark_all_unavailable()
                await self._sleep(backoff_max)
            except TransientError as exc:
                _LOGGER.warning("Erreur temporaire : %s. Backoff %ss.", exc, backoff)
                self._mark_all_unavailable()
                await self._sleep(backoff)
                backoff = min(backoff_max, backoff * 2)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — la boucle ne doit jamais mourir
                _LOGGER.exception("Erreur inattendue dans la boucle : %s", exc)
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

    async def stop(self) -> None:
        """Appelé par le driver au shutdown."""
        self._stopped = True
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
