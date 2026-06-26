"""HAP bridge for Overkiz/Cozytouch — one accessory per detected capability.

A single **bridge** (`CozytouchBridge`) hosts N child accessories, each exposing
ONE capability (sensor or actuator), each assignable to its own room in the Home
app. Children are generic (see devices.py) and built from the `exposed:` list of
the config (detected & chosen via `configure`).

- Stable AIDs: bridge = AID 1; each child carries its frozen AID (config.assign_aids).
- Polling + backoff live in `run()` (same asyncio loop as HAP-python). The bridge
  reads each device's states once per cycle and pushes them to children via
  `update()`. Writes go the other way: child setters call `send_command()`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from pyhap.accessory import Bridge

from .config import FEATURE_ORDER, IMPLEMENTED_FEATURES, resolve_password
from .devices import ACCESSORY_BY_TYPE
from .overkiz_client import AuthError, CozytouchClient, TransientError

_LOGGER = logging.getLogger(__name__)

# Libellés legacy (ancien schéma features/sensors).
_LEGACY_SENSOR_NAMES = {
    "temp_ambiante": "Température ambiante",
    "temp_exterieure": "Température extérieure",
    "temp_ecs": "Température ECS",
}


def _legacy_aid(feature: str) -> int:
    return 2 + FEATURE_ORDER.index(feature)


class CozytouchBridge(Bridge):
    """HAP bridge: one accessory per exposed capability."""

    def __init__(self, driver, cfg: dict[str, Any]):
        super().__init__(driver, cfg["homekit"]["name"])
        self._cfg = cfg
        self._client: CozytouchClient | None = None
        self._components: list[Any] = []
        self._stopped = False
        self._run_task: asyncio.Task | None = None
        self._web: Any = None
        # Cache des states (alimenté par un seed get_state + les événements live).
        self._states: dict[str, dict[str, Any]] = {}
        self._seeded = False
        self._last_reseed = 0.0
        # State surfaced to the status web page.
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
        """Convert the old features/sensors schema into `exposed` entries."""
        features = self._cfg.get("features", {})
        sensors = self._cfg.get("sensors", {})
        out: list[dict[str, Any]] = []
        for feature in FEATURE_ORDER:
            if not features.get(feature) or feature not in IMPLEMENTED_FEATURES:
                continue
            mapping = sensors.get(feature, {})
            device_url = mapping.get("device_url") or (
                self._cfg["device"].get(
                    "ecs_url" if mapping.get("device") == "ecs" else "pac_url", ""
                )
            )
            state = mapping.get("state", "")
            if device_url and state:
                out.append({
                    "aid": _legacy_aid(feature),
                    "type": "temperature_sensor",
                    "name": _LEGACY_SENSOR_NAMES.get(feature, feature),
                    "device_url": device_url,
                    "spec": {"state": state},
                })
        return out

    def _build(self) -> None:
        exposed = self._cfg.get("exposed") or self._legacy_exposed()
        for entry in exposed:
            acc_cls = ACCESSORY_BY_TYPE.get(entry.get("type"))
            if acc_cls is None:
                _LOGGER.warning("Type d'accessoire inconnu, ignoré : %r", entry.get("type"))
                continue
            device_url = entry.get("device_url", "")
            aid = entry.get("aid")
            # Backward-compat: old entries had a flat `state` instead of `spec`.
            spec = entry.get("spec") or (
                {"state": entry["state"]} if entry.get("state") else {}
            )
            if not device_url or not spec or not isinstance(aid, int):
                _LOGGER.error("Entrée exposée invalide (ignorée) : %r", entry)
                continue
            try:
                acc = acc_cls(
                    self.driver, self, entry.get("name", entry["type"]),
                    device_url, spec, aid,
                )
            except Exception as exc:  # noqa: BLE001 — un accessoire ne doit pas tout casser
                _LOGGER.error("Construction de l'accessoire échouée (%r) : %s", entry, exc)
                continue
            self.add_accessory(acc)
            self._components.append(acc)
            _LOGGER.info(
                "Accessoire '%s' (AID %s, %s) @ %s",
                entry.get("name"), aid, entry.get("type"), device_url,
            )
        if not self._components:
            _LOGGER.warning("Aucun accessoire exposé. Lancez `configure` pour en choisir.")

    @property
    def _device_urls(self) -> list[str]:
        urls: list[str] = []
        for comp in self._components:
            for url in comp.device_urls:
                if url not in urls:
                    urls.append(url)
        return urls

    # ── Écriture ─────────────────────────────────────────────────────────────
    async def send_command(
        self, device_url: str, name: str, params: list[Any] | None = None
    ) -> None:
        """Envoie une commande Overkiz (appelé par les setters des accessoires)."""
        if self._client is None:
            _LOGGER.warning("Commande %s ignorée (client non prêt).", name)
            return
        try:
            await self._client.execute(device_url, name, params or [])
            _LOGGER.info("Commande envoyée : %s%s @ %s", name, params or "", device_url)
            # Rafraîchit rapidement pour refléter le nouvel état (best-effort).
            self.driver.async_add_job(self._refresh_soon())
        except (AuthError, TransientError) as exc:
            _LOGGER.warning("Commande %s échouée : %s", name, exc)

    async def _refresh_soon(self) -> None:
        await asyncio.sleep(3)
        try:
            events = await self._client.fetch_events()
            self._apply_events(events)
            for comp in self._components:
                comp.update(self._states)
        except Exception:  # noqa: BLE001
            pass

    # ── Boucle de fonctionnement ─────────────────────────────────────────────
    async def run(self) -> None:
        cz = self._cfg["cozytouch"]
        poll = self._cfg["polling"]
        # `interval` = cadence de relecture complète (get_state) pour réconcilier.
        # On sonde les événements live plus souvent pour garder le listener actif
        # et rester frais (le listener Overkiz expire s'il n'est pas sondé).
        self._reseed_interval = int(poll["interval"])
        event_interval = min(30, self._reseed_interval)
        backoff_base = int(poll["backoff_base"])
        backoff_max = int(poll["backoff_max"])

        self._client = CozytouchClient(
            cz["username"], resolve_password(self._cfg), cz["server"]
        )
        backoff = backoff_base
        self._run_task = asyncio.current_task()

        await self._start_web()

        while not self._stopped:
            try:
                await self._cycle()
                self.connected = True
                self.last_error = None
                self.last_poll_ok = datetime.now()
                backoff = backoff_base
                await self._sleep(event_interval)
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

    async def _cycle(self) -> None:
        """Un tour : événements live (+ enregistrement listener), seed initial et
        réconciliation périodique. Les événements live sont appliqués APRÈS le
        seed pour qu'ils l'emportent sur la relecture get_state."""
        assert self._client is not None
        import time

        now = time.monotonic()
        need_seed = (not self._seeded) or (now - self._last_reseed >= self._reseed_interval)
        # fetch_events enregistre le listener au 1er appel et le garde actif.
        events = await self._client.fetch_events()
        if need_seed:
            await self._seed()
            self._seeded = True
            self._last_reseed = now
        self._apply_events(events)
        for comp in self._components:
            comp.update(self._states)

    async def _seed(self) -> None:
        """Relecture complète des states (réconciliation ; le listener actif les
        garde frais côté cloud)."""
        assert self._client is not None
        for url in self._device_urls:
            self._states[url] = await self._client.read_states(url)

    def _apply_events(self, events: list[Any]) -> None:
        """Fusionne les changements de states reçus du listener dans le cache."""
        for ev in events or []:
            url = getattr(ev, "device_url", None)
            device_states = getattr(ev, "device_states", None)
            if not url or not device_states:
                continue
            cache = self._states.setdefault(url, {})
            for st in device_states:
                if isinstance(st, dict):
                    name, value = st.get("name"), st.get("value")
                else:
                    name, value = getattr(st, "name", None), getattr(st, "value", None)
                if name is not None:
                    cache[name] = value

    def _mark_all_unavailable(self) -> None:
        for comp in self._components:
            comp.set_unavailable()

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def _start_web(self) -> None:
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
