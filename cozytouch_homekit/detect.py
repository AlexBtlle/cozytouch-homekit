"""Generic detection of HomeKit-mappable capabilities from Overkiz devices.

Detection is by *capability*, not by device model: we look at which `states`
and `commands` a device exposes and infer the HomeKit accessory type + the
command/state names to use (the `spec`). This works for any Overkiz device that
follows the usual conventions — present or future.

Each detected capability becomes a checkbox in `configure`; the chosen ones are
stored in `config.yaml` (`exposed:`) and built by the bridge (see devices.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Capability:
    key: str                 # stable id: "<device_url>::<type-or-state>"
    type: str                # one of devices.ACCESSORY_BY_TYPE keys
    name: str                # proposed tile name (renamable in Home)
    category: str            # human group label (configure display)
    device_url: str
    spec: dict[str, Any] = field(default_factory=dict)
    summary: str = ""        # current state(s) for display


# ── Extraction helpers ───────────────────────────────────────────────────────
def _state_names(device: Any) -> set[str]:
    return {
        getattr(s, "name", None)
        for s in getattr(device, "states", []) or []
        if getattr(s, "name", None)
    }


def _state_values(device: Any) -> dict[str, Any]:
    out = {}
    for s in getattr(device, "states", []) or []:
        n = getattr(s, "name", None)
        if n:
            out[n] = getattr(s, "value", None)
    return out


def _command_names(device: Any) -> set[str]:
    names: set[str] = set()
    definition = getattr(device, "definition", None)
    for c in getattr(definition, "commands", []) or []:
        n = getattr(c, "command_name", getattr(c, "name", None))
        if n:
            names.add(n)
    return names


def _first(candidates: set[str], *names: str) -> str | None:
    for n in names:
        if n in candidates:
            return n
    return None


def _label(device: Any) -> str:
    label = str(getattr(device, "label", "") or "").strip()
    if label:
        return label
    cname = str(getattr(device, "controllable_name", "") or "")
    return cname.split(":")[-1] if cname else "Appareil"


# ── State classification (sensors) ───────────────────────────────────────────
_SETPOINT_HINTS = ("target", "setpoint", "consigne")


def _is_temp_measure(n: str) -> bool:
    s = n.lower()
    return "temperature" in s and not any(h in s for h in _SETPOINT_HINTS)


def _is_temp_setpoint(n: str) -> bool:
    s = n.lower()
    return "temperature" in s and any(h in s for h in _SETPOINT_HINTS)


# state name → (homekit type, category, default name suffix)
_BINARY_SENSORS = {
    "core:SmokeState": ("smoke_sensor", "Fumée", "Fumée"),
    "core:OccupancyState": ("occupancy_sensor", "Présence", "Présence"),
    "core:MotionState": ("motion_sensor", "Mouvement", "Mouvement"),
    "core:MovementState": ("motion_sensor", "Mouvement", "Mouvement"),
    "core:ContactState": ("contact_sensor", "Contact", "Contact"),
    "core:WaterDetectionState": ("leak_sensor", "Fuite d'eau", "Fuite"),
}


def _onoff_spec(cmds: set[str], state: str | None) -> dict[str, Any]:
    spec: dict[str, Any] = {"state": state or "core:OnOffState"}
    if "on" in cmds and "off" in cmds:
        spec["on"], spec["off"] = "on", "off"
    elif "setOnOff" in cmds:
        spec["set"] = "setOnOff"
    return spec


def _friendly_sensor(label: str, cname: str, sname: str) -> str:
    c = cname.lower()
    if "outside" in c or "outdoor" in c:
        return "Température extérieure"
    if "zonetemperaturesensor" in c:
        return "Température ambiante"
    pretty = sname.replace("core:", "").replace("io:", "").replace("State", "").strip()
    return f"{label} — {pretty}"


# ── Per-device detection ─────────────────────────────────────────────────────
def _detect_actuator(device, cmds, states, statevals) -> Capability | None:
    url = str(getattr(device, "device_url", ""))
    label = _label(device)
    ui = str(getattr(device, "ui_class", "") or "")
    widget = str(getattr(device, "widget", "") or "")
    cname = str(getattr(device, "controllable_name", "") or "")

    has_onoff = ("on" in cmds and "off" in cmds) or "setOnOff" in cmds
    onoff_state = _first(states, "core:OnOffState")

    def cap(type_, category, spec):
        return Capability(f"{url}::{type_}", type_, label, category, url, spec)

    # 1) Roller shutter / blind
    pos_state = _first(states, "core:ClosureState", "core:DeploymentState",
                       "core:ClosurePositionState", "core:TargetClosureState")
    set_pos = _first(cmds, "setClosure", "setPosition", "setDeployment")
    if pos_state and (set_pos or ({"open", "close"} & cmds)):
        spec = {"pos_state": pos_state}
        if set_pos:
            spec["set"] = set_pos
        for k in ("open", "close", "stop"):
            if k in cmds:
                spec[k] = k
        return cap("window_covering", "Volet / store", spec)

    # 2) Garage door
    if ("garage" in ui.lower() or "garage" in widget.lower()) and ({"open", "close", "cycle"} & cmds):
        state = _first(states, "core:OpenClosedState", "core:DoorState") or "core:OpenClosedState"
        spec = {"state": state,
                "open": "open" if "open" in cmds else "cycle",
                "close": "close" if "close" in cmds else "cycle"}
        return cap("garage", "Porte de garage", spec)

    # 3) Lock
    lock_state = _first(states, "core:LockedUnlockedState")
    if lock_state or ({"lock", "unlock"} & cmds):
        spec = {"state": lock_state or "core:LockedUnlockedState",
                "lock": "lock" if "lock" in cmds else None,
                "unlock": "unlock" if "unlock" in cmds else None}
        return cap("lock", "Serrure", spec)

    # 4) Thermostat / heating — any device with a settable target temperature.
    set_target = _first(cmds, "setHeatingTargetTemperature", "setTargetTemperature",
                        "setComfortHeatingTargetTemperature", "setComfortTargetTemperature",
                        "setDerogatedTargetTemperature", "setThermostatSetpoint")
    if not set_target:
        set_target = next(
            (c for c in sorted(cmds)
             if c.lower().startswith("set") and "targettemperature" in c.lower()),
            None,
        )
    if set_target:
        target_state = _first(states, "core:HeatingTargetTemperatureState",
                              "core:TargetTemperatureState",
                              "core:ComfortHeatingTargetTemperatureState",
                              "core:ComfortTargetTemperatureState",
                              "core:DerogatedTargetTemperatureState",
                              "core:WaterTargetTemperatureState")
        if not target_state:
            target_state = next(
                (s for s in sorted(states) if "targettemperature" in s.lower()), None
            )
        if target_state:
            spec = {"target_state": target_state, "set_target": set_target}
            cur = _first(states, "core:TemperatureState", "core:RoomTemperatureState")
            if cur:
                spec["current_state"] = cur
            mode = _first(states, "core:OperatingModeState", "core:HeatingOnOffState",
                          "core:OnOffState", "io:PassAPCHeatingModeState")
            if mode:
                spec["mode_state"] = mode
            return cap("thermostat", "Thermostat / chauffage", spec)

    # 5) Light
    if has_onoff and ("light" in ui.lower() or "light" in widget.lower() or "light" in cname.lower()):
        spec = _onoff_spec(cmds, onoff_state)
        bstate = _first(states, "core:LightIntensityState", "core:IntensityState")
        if bstate:
            spec["bright_state"] = bstate
        if "setIntensity" in cmds:
            spec["set_bright"] = "setIntensity"
        return cap("light", "Lumière", spec)

    # 6) Outlet / plug
    if has_onoff and ("plug" in ui.lower() or "plug" in widget.lower() or "outlet" in cname.lower()):
        return cap("outlet", "Prise", _onoff_spec(cmds, onoff_state))

    # 7) Generic switch (fallback)
    if has_onoff:
        return cap("switch", "Interrupteur", _onoff_spec(cmds, onoff_state))

    return None


def _detect_sensors(device, states, statevals) -> list[Capability]:
    url = str(getattr(device, "device_url", ""))
    label = _label(device)
    cname = str(getattr(device, "controllable_name", "") or "")
    caps: list[Capability] = []

    def add(type_, category, name, sname):
        caps.append(Capability(
            f"{url}::{sname}", type_, name, category, url,
            spec={"state": sname},
            summary=f"{sname} = {statevals.get(sname)}",
        ))

    for sname in states:
        if _is_temp_measure(sname):
            add("temperature_sensor", "Température (mesure)",
                _friendly_sensor(label, cname, sname), sname)
        elif _is_temp_setpoint(sname):
            add("temperature_setpoint", "Consigne température (lecture)",
                _friendly_sensor(label, cname, sname), sname)
        elif "humidity" in sname.lower():
            add("humidity_sensor", "Humidité", f"{label} — humidité", sname)
        elif sname in _BINARY_SENSORS:
            type_, category, suffix = _BINARY_SENSORS[sname]
            add(type_, category, f"{label} — {suffix}", sname)
    return caps


def _detect_device(device) -> list[Capability]:
    cmds = _command_names(device)
    states = _state_names(device)
    statevals = _state_values(device)

    caps: list[Capability] = []
    actuator = _detect_actuator(device, cmds, states, statevals)
    sensors = _detect_sensors(device, states, statevals)
    if actuator is not None:
        # enrich summary with the primary state value if any
        primary = (actuator.spec.get("state") or actuator.spec.get("pos_state")
                   or actuator.spec.get("target_state"))
        if primary:
            actuator.summary = f"{primary} = {statevals.get(primary)}"
        caps.append(actuator)
        # A thermostat already exposes the setpoint (writable) → drop the
        # redundant read-only setpoint sensors for this device.
        if actuator.type == "thermostat":
            sensors = [s for s in sensors if s.type != "temperature_setpoint"]
    caps.extend(sensors)
    return caps


def detect_capabilities(devices: list[Any]) -> list[Capability]:
    """Return every HomeKit-mappable capability across all devices."""
    caps: list[Capability] = []
    seen: set[str] = set()
    for device in devices:
        for cap in _detect_device(device):
            if cap.key in seen:
                continue
            seen.add(cap.key)
            caps.append(cap)
    caps.sort(key=lambda c: (c.category, c.name, c.key))
    return caps
