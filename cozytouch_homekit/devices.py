"""Generic HomeKit accessory classes mapping Overkiz capabilities.

Each accessory is built from a detected *capability*: a `type` (the HomeKit
accessory kind) plus a `spec` dict resolving the Overkiz state/command names for
this device. The same classes work for any Overkiz device that follows the
pattern — they are not tied to a device model.

Write path: writable characteristics register a `setter_callback`; when HomeKit
writes, the callback schedules an Overkiz command on the driver's event loop via
the bridge's `send_command` coroutine.
"""

from __future__ import annotations

import logging
from typing import Any

from pyhap.accessory import Accessory
from pyhap import const as _const

_LOGGER = logging.getLogger(__name__)


def _cat(name: str) -> int:
    """CATEGORY_* by name, fallback to a generic category."""
    return getattr(_const, name, getattr(_const, "CATEGORY_OTHER", 1))


# ── Value parsing helpers ────────────────────────────────────────────────────
_TRUE_TOKENS = {"on", "true", "1", "active", "detected", "open", "opened",
                "personinside", "locked", "wet", "humid", "yes"}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in _TRUE_TOKENS


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── Base ─────────────────────────────────────────────────────────────────────
class BaseAccessory(Accessory):
    """Base for one Overkiz capability = one HomeKit accessory."""

    category = _cat("CATEGORY_SENSOR")
    kind = "sensor"          # neutral key, translated by the web layer
    writable = False

    def __init__(self, driver, controller, name, device_url, spec, aid):
        super().__init__(driver, name, aid=aid)
        self._controller = controller    # the bridge (send_command, driver)
        self._name = name
        self.device_url = device_url
        self.spec = spec or {}
        self._last: Any = None
        self._available = False

        info = self.get_service("AccessoryInformation")
        info.configure_char("Manufacturer", value="Atlantic")
        info.configure_char("Model", value="Cozytouch")
        info.configure_char("SerialNumber", value=f"cozytouch-{aid}")
        info.configure_char("FirmwareRevision", value="1.0.0")

    # plumbing -----------------------------------------------------------------
    @property
    def device_urls(self) -> list[str]:
        return [self.device_url]

    def _send(self, command: str, params: list[Any] | None = None) -> None:
        """Schedule an Overkiz command from a (sync) setter callback."""
        if not command:
            return
        self.driver.async_add_job(
            self._controller.send_command, self.device_url, command, list(params or [])
        )

    def set_unavailable(self) -> None:
        self._available = False

    def update(self, states_by_url: dict[str, dict[str, Any]]) -> None:  # pragma: no cover
        raise NotImplementedError

    def status(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "kind": self.kind,
            "unit": "",
            "value": self._last,
            "available": self._available,
            "device_url": self.device_url,
            "state": self.spec.get("state", ""),
        }


# ── Sensors (read-only) ──────────────────────────────────────────────────────
TEMP_MIN, TEMP_MAX = -50.0, 100.0
FAULT_NONE, FAULT_GENERAL = 0, 1


class _SensorBase(BaseAccessory):
    """Sensor with StatusActive/StatusFault."""

    service_name = ""
    main_char = ""

    def __init__(self, driver, controller, name, device_url, spec, aid):
        super().__init__(driver, controller, name, device_url, spec, aid)
        svc = self.add_preload_service(
            self.service_name, chars=["StatusActive", "StatusFault"]
        )
        self._char = svc.get_characteristic(self.main_char)
        self._char_active = svc.get_characteristic("StatusActive")
        self._char_fault = svc.get_characteristic("StatusFault")
        self._configure(svc)
        self.set_unavailable()

    def _configure(self, svc) -> None:
        pass

    def _convert(self, raw: Any) -> Any:  # pragma: no cover - per type
        raise NotImplementedError

    def update(self, states_by_url):
        raw = states_by_url.get(self.device_url, {}).get(self.spec.get("state"))
        if raw is None:
            self.set_unavailable()
            return
        try:
            self._char.set_value(self._convert(raw))
            self._available = True
            self._last = raw
            self._char_active.set_value(True)
            self._char_fault.set_value(FAULT_NONE)
        except (TypeError, ValueError):
            self.set_unavailable()

    def set_unavailable(self):
        self._available = False
        self._char_active.set_value(False)
        self._char_fault.set_value(FAULT_GENERAL)


class TemperatureSensor(_SensorBase):
    kind, unit = "temperature", "°C"
    service_name, main_char = "TemperatureSensor", "CurrentTemperature"

    def _configure(self, svc):
        self._char.override_properties(
            properties={"minValue": TEMP_MIN, "maxValue": TEMP_MAX, "minStep": 0.1}
        )

    def _convert(self, raw):
        v = _num(raw)
        if v is None:
            raise ValueError(raw)
        return max(TEMP_MIN, min(TEMP_MAX, v))

    def status(self):
        s = super().status(); s["unit"] = "°C"; return s


class HumiditySensor(_SensorBase):
    kind, unit = "humidity", "%"
    service_name, main_char = "HumiditySensor", "CurrentRelativeHumidity"

    def _convert(self, raw):
        v = _num(raw)
        if v is None:
            raise ValueError(raw)
        return max(0.0, min(100.0, v))

    def status(self):
        s = super().status(); s["unit"] = "%"; return s


class _BinarySensor(_SensorBase):
    """Boolean sensor; maps a state value → 0/1."""

    invert = False

    def _convert(self, raw):
        on = _truthy(raw)
        return int((not on) if self.invert else on)


class ContactSensor(_BinarySensor):
    kind = "contact"
    service_name, main_char = "ContactSensor", "ContactSensorState"
    # ContactSensorState: 0 = detected (closed), 1 = not detected (open).
    # _truthy("open")=True→1, _truthy("closed")=False→0 → no inversion needed.


class MotionSensor(_BinarySensor):
    kind = "motion"
    service_name, main_char = "MotionSensor", "MotionDetected"


class OccupancySensor(_BinarySensor):
    kind = "occupancy"
    service_name, main_char = "OccupancySensor", "OccupancyDetected"


class SmokeSensor(_BinarySensor):
    kind = "smoke"
    service_name, main_char = "SmokeSensor", "SmokeDetected"


class LeakSensor(_BinarySensor):
    kind = "leak"
    service_name, main_char = "LeakSensor", "LeakDetected"


# ── Actuators (read + write) ─────────────────────────────────────────────────
class _OnOff(BaseAccessory):
    """on/off accessory (Switch/Outlet/Lightbulb)."""

    writable = True
    service_name = "Switch"
    extra_chars: list[str] = []

    def __init__(self, driver, controller, name, device_url, spec, aid):
        super().__init__(driver, controller, name, device_url, spec, aid)
        chars = list(self.extra_chars) + self._dynamic_chars()
        svc = self.add_preload_service(self.service_name, chars=chars)
        self._char_on = svc.get_characteristic("On")
        svc.configure_char("On", setter_callback=self._set_on)
        self._svc = svc
        self._extra_setup(svc)

    def _dynamic_chars(self) -> list[str]:
        return []

    def _extra_setup(self, svc):
        pass

    def _set_on(self, value: bool):
        cmd = self.spec.get("on" if value else "off")
        if cmd:
            self._send(cmd)
        elif self.spec.get("set"):  # setOnOff-style
            self._send(self.spec["set"], [1 if value else 0])

    def update(self, states_by_url):
        raw = states_by_url.get(self.device_url, {}).get(self.spec.get("state"))
        if raw is None:
            self.set_unavailable()
            return
        self._available = True
        self._last = raw
        self._char_on.set_value(_truthy(raw))

    def status(self):
        s = super().status()
        s["value"] = "on" if _truthy(self._last) else "off"
        return s


class Switch(_OnOff):
    category = _cat("CATEGORY_SWITCH")
    kind = "switch"
    service_name = "Switch"


class Outlet(_OnOff):
    category = _cat("CATEGORY_OUTLET")
    kind = "outlet"
    service_name = "Outlet"
    extra_chars = ["OutletInUse"]

    def _extra_setup(self, svc):
        svc.get_characteristic("OutletInUse").set_value(True)


class Lightbulb(_OnOff):
    category = _cat("CATEGORY_LIGHTBULB")
    kind = "light"
    service_name = "Lightbulb"

    def _dynamic_chars(self):
        return (
            ["Brightness"]
            if (self.spec.get("bright_state") or self.spec.get("set_bright"))
            else []
        )

    def _extra_setup(self, svc):
        self._char_bri = None
        if self.spec.get("bright_state") or self.spec.get("set_bright"):
            self._char_bri = svc.configure_char(
                "Brightness", setter_callback=self._set_bright
            )

    def _set_bright(self, value: int):
        cmd = self.spec.get("set_bright")
        if cmd:
            self._send(cmd, [int(value)])

    def update(self, states_by_url):
        super().update(states_by_url)
        states = states_by_url.get(self.device_url, {})
        bs = self.spec.get("bright_state")
        if bs and getattr(self, "_char_bri", None) is not None:
            v = _num(states.get(bs))
            if v is not None:
                self._char_bri.set_value(int(max(0, min(100, v))))


class WindowCovering(BaseAccessory):
    """Roller shutter / blind. Overkiz closure 0=open..100=closed ; HomeKit
    position 0=closed..100=open → position = 100 - closure."""

    category = _cat("CATEGORY_WINDOW_COVERING")
    kind = "cover"
    writable = True

    def __init__(self, driver, controller, name, device_url, spec, aid):
        super().__init__(driver, controller, name, device_url, spec, aid)
        svc = self.add_preload_service("WindowCovering")
        self._cur = svc.get_characteristic("CurrentPosition")
        self._tgt = svc.get_characteristic("TargetPosition")
        self._pstate = svc.get_characteristic("PositionState")
        svc.configure_char("TargetPosition", setter_callback=self._set_position)
        self._pstate.set_value(2)  # stopped

    def _set_position(self, value: int):
        value = int(max(0, min(100, value)))
        set_cmd = self.spec.get("set")
        if set_cmd:
            self._send(set_cmd, [100 - value])  # closure
        elif value >= 95 and self.spec.get("open"):
            self._send(self.spec["open"])
        elif value <= 5 and self.spec.get("close"):
            self._send(self.spec["close"])
        elif self.spec.get("close"):
            self._send(self.spec["close"])

    def update(self, states_by_url):
        raw = states_by_url.get(self.device_url, {}).get(self.spec.get("pos_state"))
        v = _num(raw)
        if v is None:
            self.set_unavailable()
            return
        pos = int(max(0, min(100, 100 - v)))
        self._available = True
        self._last = pos
        self._cur.set_value(pos)
        self._tgt.set_value(pos)
        self._pstate.set_value(2)

    def status(self):
        s = super().status()
        s["unit"] = "%"
        s["value"] = self._last if self._last is not None else "—"
        return s


class Lock(BaseAccessory):
    category = _cat("CATEGORY_DOOR_LOCK")
    kind = "lock"
    writable = True

    def __init__(self, driver, controller, name, device_url, spec, aid):
        super().__init__(driver, controller, name, device_url, spec, aid)
        svc = self.add_preload_service("LockMechanism")
        self._cur = svc.get_characteristic("LockCurrentState")
        self._tgt = svc.get_characteristic("LockTargetState")
        svc.configure_char("LockTargetState", setter_callback=self._set_target)

    def _set_target(self, value: int):
        # 1 = secured (lock), 0 = unsecured (unlock)
        cmd = self.spec.get("lock" if value == 1 else "unlock")
        if cmd:
            self._send(cmd)

    def update(self, states_by_url):
        raw = states_by_url.get(self.device_url, {}).get(self.spec.get("state"))
        if raw is None:
            self.set_unavailable()
            return
        secured = 1 if str(raw).lower() in ("locked", "closed", "true", "1") else 0
        self._available = True
        self._last = raw
        self._cur.set_value(secured)
        self._tgt.set_value(secured)

    def status(self):
        s = super().status()
        s["value"] = self._last
        return s


class GarageDoor(BaseAccessory):
    category = _cat("CATEGORY_GARAGE_DOOR_OPENER")
    kind = "garage"
    writable = True

    def __init__(self, driver, controller, name, device_url, spec, aid):
        super().__init__(driver, controller, name, device_url, spec, aid)
        svc = self.add_preload_service("GarageDoorOpener")
        self._cur = svc.get_characteristic("CurrentDoorState")
        self._tgt = svc.get_characteristic("TargetDoorState")
        svc.get_characteristic("ObstructionDetected").set_value(False)
        svc.configure_char("TargetDoorState", setter_callback=self._set_target)

    def _set_target(self, value: int):
        # 0 = open, 1 = closed
        cmd = self.spec.get("open" if value == 0 else "close")
        if cmd:
            self._send(cmd)

    def update(self, states_by_url):
        raw = states_by_url.get(self.device_url, {}).get(self.spec.get("state"))
        if raw is None:
            self.set_unavailable()
            return
        # CurrentDoorState/TargetDoorState: 0 = open, 1 = closed
        opened = str(raw).lower() in ("open", "opened", "true", "1")
        state = 0 if opened else 1
        self._available = True
        self._last = raw
        self._cur.set_value(state)
        self._tgt.set_value(state)

    def status(self):
        s = super().status()
        s["value"] = self._last
        return s


# type d'entrée `exposed` → classe d'accessoire.
ACCESSORY_BY_TYPE = {
    "temperature_sensor": TemperatureSensor,
    "temperature_setpoint": TemperatureSensor,
    "humidity_sensor": HumiditySensor,
    "contact_sensor": ContactSensor,
    "motion_sensor": MotionSensor,
    "occupancy_sensor": OccupancySensor,
    "smoke_sensor": SmokeSensor,
    "leak_sensor": LeakSensor,
    "switch": Switch,
    "outlet": Outlet,
    "light": Lightbulb,
    "window_covering": WindowCovering,
    "lock": Lock,
    "garage": GarageDoor,
}
