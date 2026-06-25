# cozytouch-homekit

[🇫🇷 Français](README.md) · **🇬🇧 English**

Expose an **Atlantic Alféa Extensa AI Duo** heat pump (or another Atlantic
device driven by **Cozytouch**) in **Apple HomeKit**, locally, on a
**Raspberry Pi Zero W**, through a **HAP bridge** (one accessory per function,
each assignable to its own room).

Data is read from the **Overkiz / Cozytouch** cloud API via
[`pyoverkiz`](https://github.com/iMicknl/python-overkiz-api) and published to
HomeKit via [HAP-python](https://github.com/ikalchev/HAP-python).

> **Status — read-only, auto-detected.** `configure` detects your account's
> capabilities (temperature/humidity sensors, setpoints…) and lets you tick
> what you expose; each choice = one bridge accessory. Control (thermostat, DHW
> boost) is planned for V2 on the same principle.

---

## In three steps

```bash
git clone https://github.com/AlexBtlle/cozytouch-homekit.git
cd cozytouch-homekit

# 1) Install (system deps, venv, piwheels requirements, systemd service)
./install.sh

# 2) Configure (launched by install.sh, re-runnable any time)
python -m cozytouch_homekit configure

# 3) Pair: start the service, scan the QR code in the Home app
sudo systemctl restart cozytouch-homekit
journalctl -u cozytouch-homekit -f
```

---

## Architecture: **bridge** (one accessory per function)

- The heat pump = **1 physical device** whose every function (ambient sensor,
  outdoor sensor, DHW, thermostat…) is exposed as a **separate HAP accessory**,
  hosted by a **bridge** (`CozytouchBridge`, subclasses `Bridge`).
- **Why a bridge** (instead of a standalone accessory): in HomeKit, **room**
  assignment is per accessory, not per service. Since the sensors are physically
  in different places (indoor / outdoor / tank), separate accessories are
  required to place them in different rooms. That is exactly what a bridge is
  for: a single pairing, N independent accessories.
- **Stable AIDs across restarts**: the bridge = AID `1`; each child accessory
  gets a **deterministic** AID derived from its position in
  `config.FEATURE_ORDER` (`feature_aid()`), stable even when functions are
  enabled/disabled. IIDs are stable within each child accessory.
- **Adding a function** later: new accessory = new tile, no re-pairing.
  **Removing** an already-paired function may leave a ghost tile (see below).

---

## Step 0 — Discovery (DO IT BEFORE mapping)

Overkiz state names **depend on your firmware**. Don't code the mapping blind:
dump your machine first.

```bash
python -m cozytouch_homekit explore                 # full dump -> explore_dump.json
python -m cozytouch_homekit explore --anonymize     # masks the gateway ID (for commits)
```

The dump lists every device, its `states` (with values) and `commands`. Look
for:

- the `device_url` of the **heat pump** (and the **DHW** component on the Duo);
- the temperature state names (e.g. `core:TemperatureState`,
  `core:OutsideTemperatureState`, …).

`configure` then reads them for you (see below).

> ⚠️ The **non-anonymized** dump contains your gateway ID: it is `.gitignore`d.
> Use `--anonymize` before committing any reference dump.

---

## Configuration

`configure` writes `config.yaml` (gitignored, `chmod 0600`). It handles:

1. **Cozytouch credentials** (login + password + server);
2. **account connection → automatic detection** of exposable capabilities;
3. **checkboxes**: you pick what to expose to HomeKit.

No mapping to write by hand: `configure` queries your Overkiz account,
recognizes the mappable states (temperatures, humidity, setpoints…) and shows
the list. Each ticked capability becomes a **bridge accessory** (its own tile,
assignable to its own room). The result is stored in `config.yaml` under
`exposed:` (see [`config.example.yaml`](config.example.yaml)):

```yaml
exposed:
  - aid: 2
    type: "temperature_sensor"
    name: "Ambient temperature"
    device_url: "io://…/…"
    state: "core:TemperatureState"
```

Types currently recognized: `temperature_sensor`, `temperature_setpoint`
(setpoint, read-only), `humidity_sensor`. Control (thermostat, DHW boost…) is
coming in V2 on the same principle (detect → choose).

### ⚠️ Changing the structure AFTER pairing

- **Before first pairing**: no problem, configure freely.
- **In service**: re-run `configure` then **restart** the service → HAP-python
  republishes with an incremented **config number (c#)** → HomeKit re-reads.
- **Adding** an accessory later: clean (new tile).
- **Removing** an already-paired accessory: may leave a **ghost tile**. Worst
  case, remove and re-add the bridge in the Home app.
- **AIDs stay stable** (pinned per `exposed` entry) → kept accessories are not
  reorganized.

---

## Data source & robustness

- **Cloud only**: Cozytouch exposes no local API → Internet dependency by design.
- **Overkiz rate-limit**: **spaced** polling (120 s by default; 90–300 s
  recommended, 30 s minimum), **exponential backoff** on error (configurable
  cap), automatic **session refresh** when the token expires.
- If the API does not respond, characteristics switch to **`StatusFault`**
  (unavailable) instead of freezing a misleading value.
- `pyoverkiz` (async/aiohttp) and HAP-python run on the **same** asyncio event
  loop: the polling loop is the bridge's `run()` coroutine, scheduled by the
  `AccessoryDriver`. No blocking thread.

---

## Tested hardware & ARMv6

- Target: **Raspberry Pi Zero W v1** (ARMv6, 1 GHz, 512 MB) + **Raspberry Pi
  OS**. Raspberry Pi OS is required for **piwheels** to be active.
  - **Bookworm** → Python 3.11 (`cp311` wheels)
  - **Trixie** → Python 3.13 (`cp313` wheels)
  - ⚠️ piwheels wheels are tagged per Python version: on Trixie, a pinned
    version that only has a `cp311` wheel falls back to building from source.
    Check `cp313`/`armv6l` availability on piwheels.
- **piwheels** provides prebuilt ARMv6 wheels → `pip` fetches binaries instead
  of building most packages from source.
- ⚠️ **Slow pairing**: the crypto handshake on single-core ARMv6 can take
  **several minutes**. It is not a crash — let it run.

### ⚠️ `cryptography` on ARMv6: via apt, not via pip

This is **the** hard part of the install, and there is no magic pip wheel:

- `cryptography ≥ 3.5` is a **Rust** package. **piwheels does not build Rust
  wheels for armv6** → `pip install cryptography` falls back to building from
  source → `error: can't find Rust compiler` (and even with Rust: hours of
  compilation + OOM risk on 512 MB).
- Pre-Rust versions (`≤ 3.4.8`) have no `cp313` wheel and do not run on Python
  3.13. **So no version of `cryptography` is pip-installable on Pi Zero +
  Trixie.**

**Solution (handled by `install.sh`)**: install `cryptography` from **Debian**
(`apt install python3-cryptography`, prebuilt by Debian, Rust included) and
create the venv with **`--system-site-packages`** so it sees it. Same for
`zeroconf` (`python3-zeroconf`). `cryptography` is therefore **deliberately
absent** from `requirements.txt`.

### Install reproducibility

`requirements.txt` is **pinned** (`==`) for pip packages. The heavy native
packages (`cryptography`, `zeroconf`) come from **apt** and follow the Debian
Trixie version. Everything else fetches `cp313`/`armv6l` wheels from piwheels.

> Validated on **Pi Zero W v1 / Raspberry Pi OS Trixie (Python 3.13)**: all pip
> deps install as wheels, `cryptography`/`zeroconf` come from apt, no
> compilation on the board.

---

## Commands

| Command | Role |
|---|---|
| `python -m cozytouch_homekit configure` | Menu: credentials + feature selection |
| `python -m cozytouch_homekit explore`   | Dump devices/states/commands (Step 0) |
| `python -m cozytouch_homekit run`       | Start the HomeKit service (used by systemd) |

systemd service: `cozytouch-homekit` (`Restart=on-failure`, start at boot,
non-privileged user). Logs: `journalctl -u cozytouch-homekit -f`.

---

## Status page

The service exposes a **mini web page** at `http://<pi>.local:8080/`
(configurable / disableable under `web:` in `config.yaml`). It shows:

- **HomeKit pairing**: QR code + PIN (always shown), with the paired badge;
- **Raspberry Pi**: hostname, IP, CPU temperature, uptime, load, RAM;
- **Overkiz**: connected or not, last read time, last error, polling interval;
- **Exposed accessories**: name, type, current value, availability.

Reload the page to refresh; a JSON endpoint is available at `/api/status`.
Served internally by **aiohttp** (already pulled by `pyoverkiz`) and
**pyqrcode** → **no extra dependency**. The page language follows the
`language` key (`en`/`fr`) in `config.yaml`.

> ⚠️ It shows the pairing PIN and system info: expose it on a **trusted
> network** only. It **never** shows the Cozytouch password.

---

## Secrets

- The **Cozytouch password is encrypted at rest**: `configure` stores it as
  `enc:v1:…` in `config.yaml`, with the key living in a separate `.secret.key`
  file (0600, gitignored). The service decrypts it at startup.
  > Threat model: protects against casual viewing / accidental sharing of
  > `config.yaml`. **Not** a vault — root access (to both files) can decrypt.
  > For OS-level hardening: `systemd-creds`.
- `config.yaml`, `.secret.key`, `.env`, `accessory.state` and the
  `explore_dump*.json` dumps are `.gitignore`d. **Never commit them.**

---

## Roadmap

- **V1**: reproducible install + `configure` + temperature sensors + QR code +
  spaced polling + systemd. ✅ Validated on Pi Zero W v1 / Trixie.
- **V1.5 (current)**: switch to a **bridge** (one accessory per function →
  room-assignable) + auto-detection + status page + i18n (en/fr).
- **V2**: writing — `Thermostat`/`HeaterCooler` (setpoint + mode), `Switch` for
  DHW boost, based on the full dump (states + commands). ⚠️ HomeKit has no
  "weather-compensated heat pump" type: mapping to assume and document.
- **V3 (optional)**: offline cache, healthcheck, ghost-tile cleanup procedure.

## License

GPL-3.0 — see [LICENSE](LICENSE).
