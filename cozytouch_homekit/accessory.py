"""Accessoire HAP standalone (PAS un bridge) pour la PAC Atlantic.

1 device physique = 1 accessoire = N services. Les services sont ajoutés dans
un ORDRE CANONIQUE STABLE (cf. config.FEATURE_ORDER) pour garder des IID
stables entre redémarrages. L'AID d'un accessoire standalone vaut 1.

V1 : services TemperatureSensor en lecture seule. Le polling Overkiz et le
backoff vivent dans la méthode async `run()` (même event loop que HAP-python).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyhap.accessory import Accessory
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

# Bornes HomeKit pour CurrentTemperature (°C). On élargit la plage basse pour
# accepter des températures extérieures négatives.
TEMP_MIN = -50.0
TEMP_MAX = 100.0

# StatusFault : 0 = NO_FAULT, 1 = GENERAL_FAULT.
FAULT_NONE = 0
FAULT_GENERAL = 1


class TempSensorBinding:
    """Lie un service TemperatureSensor à un (device_url, state) Overkiz."""

    def __init__(self, feature: str, service, device_url: str, state_name: str):
        self.feature = feature
        self.service = service
        self.device_url = device_url
        self.state_name = state_name
        self.char_temp = service.get_characteristic("CurrentTemperature")
        self.char_fault = service.get_characteristic("StatusFault")
        self.char_active = service.get_characteristic("StatusActive")
        self._warned_missing = False  # n'avertir qu'une fois du state introuvable

    def set_temperature(self, value: float) -> None:
        clamped = max(TEMP_MIN, min(TEMP_MAX, float(value)))
        self.char_temp.set_value(clamped)
        if self.char_active:
            self.char_active.set_value(True)
        if self.char_fault:
            self.char_fault.set_value(FAULT_NONE)

    def set_unavailable(self) -> None:
        """API muette : on signale un défaut plutôt que de figer une valeur trompeuse."""
        if self.char_active:
            self.char_active.set_value(False)
        if self.char_fault:
            self.char_fault.set_value(FAULT_GENERAL)


class CozytouchAccessory(Accessory):
    category = CATEGORY_SENSOR

    def __init__(self, driver, cfg: dict[str, Any]):
        super().__init__(driver, cfg["homekit"]["name"])
        self._cfg = cfg
        self._client: CozytouchClient | None = None
        self._bindings: list[TempSensorBinding] = []
        self._stopped = False
        self._run_task: asyncio.Task | None = None

        self._set_info()
        self._build_services()

    # ── Construction ─────────────────────────────────────────────────────────
    def _set_info(self) -> None:
        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="Atlantic")
        info.configure_char("Model", value="Alfea Extensa AI Duo")
        info.configure_char("SerialNumber", value=self._cfg["device"].get("pac_url") or "cozytouch-pac")
        info.configure_char("FirmwareRevision", value="1.0.0")

    def _resolve_url(self, device_key: str) -> str:
        if device_key == "ecs":
            return self._cfg["device"].get("ecs_url", "")
        return self._cfg["device"].get("pac_url", "")

    def _build_services(self) -> None:
        features = self._cfg.get("features", {})
        sensors = self._cfg.get("sensors", {})

        # Ordre canonique stable → IID stables.
        for feature in FEATURE_ORDER:
            if not features.get(feature):
                continue
            if feature not in IMPLEMENTED_FEATURES:
                _LOGGER.warning(
                    "Feature '%s' activée mais non implémentée en V1 — ignorée.", feature
                )
                continue

            mapping = sensors.get(feature, {})
            # device_url direct prioritaire ; repli legacy sur device: pac/ecs.
            device_url = mapping.get("device_url") or self._resolve_url(
                mapping.get("device", "pac")
            )
            state_name = mapping.get("state", "")
            if not device_url or not state_name:
                _LOGGER.error(
                    "Feature '%s' activée mais device_url/state manquant "
                    "(device_url=%r state=%r) — service non créé. Lancez `explore` "
                    "puis `configure`.",
                    feature, device_url, state_name,
                )
                continue

            service = self.add_preload_service(
                "TemperatureSensor",
                chars=["Name", "StatusActive", "StatusFault"],
            )
            service.configure_char("Name", value=SENSOR_NAMES.get(feature, feature))
            # CurrentTemperature : élargir les bornes pour les valeurs négatives.
            temp_char = service.get_characteristic("CurrentTemperature")
            temp_char.override_properties(
                properties={"minValue": TEMP_MIN, "maxValue": TEMP_MAX, "minStep": 0.1}
            )
            binding = TempSensorBinding(feature, service, device_url, state_name)
            binding.set_unavailable()  # état initial tant qu'on n'a pas lu
            self._bindings.append(binding)
            _LOGGER.info(
                "Service TemperatureSensor '%s' ← %s @ %s",
                feature, state_name, device_url,
            )

        if not self._bindings:
            _LOGGER.warning(
                "Aucun service de capteur créé. Vérifiez features + device_url + states."
            )

    # ── Boucle de fonctionnement ─────────────────────────────────────────────
    @property
    def _device_urls(self) -> list[str]:
        urls: list[str] = []
        for b in self._bindings:
            if b.device_url not in urls:
                urls.append(b.device_url)
        return urls

    async def run(self) -> None:
        """Boucle de polling (lancée une fois par le driver, dans sa loop)."""
        cz = self._cfg["cozytouch"]
        poll = self._cfg["polling"]
        interval = int(poll["interval"])
        backoff_base = int(poll["backoff_base"])
        backoff_max = int(poll["backoff_max"])

        self._client = CozytouchClient(cz["username"], cz["password"], cz["server"])
        backoff = backoff_base
        # Référence à notre propre tâche pour une annulation propre dans stop().
        self._run_task = asyncio.current_task()

        while not self._stopped:
            try:
                await self._poll_once()
                backoff = backoff_base  # succès → reset du backoff
                await self._sleep(interval)
            except AuthError as exc:
                # Identifiants faux : inutile de marteler l'API. On marque
                # indisponible et on patiente longtemps.
                _LOGGER.error("%s — corrigez `configure`. Pause prolongée.", exc)
                self._mark_all_unavailable()
                await self._sleep(backoff_max)
            except TransientError as exc:
                _LOGGER.warning(
                    "Erreur temporaire : %s. Backoff %ss.", exc, backoff
                )
                self._mark_all_unavailable()
                await self._sleep(backoff)
                backoff = min(backoff_max, backoff * 2)  # backoff exponentiel
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — la boucle ne doit jamais mourir
                _LOGGER.exception("Erreur inattendue dans la boucle : %s", exc)
                self._mark_all_unavailable()
                await self._sleep(backoff)
                backoff = min(backoff_max, backoff * 2)

    async def _poll_once(self) -> None:
        assert self._client is not None
        # Une lecture par device, partagée entre bindings du même device.
        states_by_url: dict[str, dict[str, Any]] = {}
        for url in self._device_urls:
            states_by_url[url] = await self._client.read_states(url)

        for binding in self._bindings:
            states = states_by_url.get(binding.device_url, {})
            value = states.get(binding.state_name)
            if value is None:
                if not binding._warned_missing:
                    # Mapping probablement erroné → lister ce qui EXISTE vraiment.
                    temp_states = sorted(
                        n for n in states if "emperatur" in n.lower()
                    )
                    _LOGGER.warning(
                        "State '%s' introuvable sur %s (capteur '%s'). "
                        "States de température disponibles : %s. "
                        "Corrigez `sensors.%s.state` dans config.yaml.",
                        binding.state_name, binding.device_url, binding.feature,
                        ", ".join(temp_states) or "(aucun)", binding.feature,
                    )
                    binding._warned_missing = True
                binding.set_unavailable()
                continue
            binding._warned_missing = False
            try:
                binding.set_temperature(float(value))
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Valeur non numérique pour %s : %r", binding.feature, value
                )
                binding.set_unavailable()

    def _mark_all_unavailable(self) -> None:
        for binding in self._bindings:
            binding.set_unavailable()

    async def _sleep(self, seconds: float) -> None:
        """Sleep interruptible par stop()."""
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise

    async def stop(self) -> None:
        """Appelé par le driver au shutdown."""
        self._stopped = True
        # Annuler proprement la boucle run() (évite « Task was destroyed but it
        # is pending! » au shutdown).
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
