"""Mini status web page (default port 8080).

Serves a small LAN dashboard:
  - HomeKit pairing (QR + PIN, always shown);
  - Pi status (hostname, IP, CPU temperature, uptime, load, RAM);
  - Overkiz status (connected, last read, last error, polling interval);
  - exposed accessories and their current values.

Runs in the SAME event loop as the HAP bridge (aiohttp, already pulled by
pyoverkiz → no extra dependency). `render_html()` is a pure function, testable
without a server. Language is driven by the `language` config key (en/fr).

⚠️ The page shows the pairing PIN and system info: expose it on a trusted
network only (it NEVER shows the Cozytouch password).
"""

from __future__ import annotations

import html
import logging
import os
import socket
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)


# ── System info (dependency-free, Linux / Raspberry Pi OS target) ────────────
def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def cpu_temp_c() -> float | None:
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    if raw and raw.lstrip("-").isdigit():
        return round(int(raw) / 1000.0, 1)
    return None


def uptime_str() -> str | None:
    raw = _read("/proc/uptime")
    if not raw:
        return None
    try:
        secs = int(float(raw.split()[0]))
    except (ValueError, IndexError):
        return None
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{mins}min")
    return " ".join(parts)


def mem_str() -> str | None:
    raw = _read("/proc/meminfo")
    if not raw:
        return None
    info = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            info[parts[0][:-1]] = int(parts[1])  # kB
    total = info.get("MemTotal")
    avail = info.get("MemAvailable")
    if not total:
        return None
    used = total - (avail or 0)
    return f"{used // 1024} / {total // 1024} MB"


def load_str() -> str | None:
    try:
        a, b, c = os.getloadavg()
        return f"{a:.2f} {b:.2f} {c:.2f}"
    except OSError:
        return None


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "?"


def _qr_svg(uri: str) -> str | None:
    """Inline SVG of the pairing QR (via pyqrcode). None if unavailable."""
    try:
        import io

        import pyqrcode

        buf = io.BytesIO()
        pyqrcode.create(uri).svg(buf, scale=5, quiet_zone=2)
        svg = buf.getvalue().decode("utf-8")
        # Strip the XML prolog for clean inlining in HTML.
        if svg.startswith("<?xml"):
            svg = svg.split("?>", 1)[-1].lstrip()
        return svg
    except Exception:  # noqa: BLE001 — the QR is a bonus
        return None


# ── i18n ─────────────────────────────────────────────────────────────────────
LOCALES = {
    "en": {
        "lang": "en", "title_suffix": "status",
        "homekit_pairing": "HomeKit pairing",
        "paired": "✓ Paired",
        "repair_note": "To re-pair: remove the accessory in the Home app, then re-scan.",
        "not_paired": "Not paired — scan the QR code in the Home app",
        "pin": "PIN code", "hap_port": "HAP port",
        "raspberry_pi": "Raspberry Pi",
        "hostname": "Hostname", "local_ip": "Local IP", "cpu_temp": "CPU temperature",
        "uptime": "Uptime", "load": "Load", "ram_used": "RAM used",
        "overkiz": "Overkiz / Cozytouch", "state": "State",
        "connected": "connected", "disconnected": "disconnected",
        "last_read": "Last read", "interval": "Interval", "last_error": "Last error",
        "exposed": "Exposed accessories",
        "c_name": "Name", "c_type": "Type", "c_value": "Value", "c_state": "State",
        "ok": "ok", "unavailable": "unavailable", "none": "No accessory exposed.",
        "footer": "Generated at {t} · reload the page to refresh",
        "temperature": "temperature", "humidity": "humidity",
    },
    "fr": {
        "lang": "fr", "title_suffix": "statut",
        "homekit_pairing": "Appairage HomeKit",
        "paired": "✓ Appairé",
        "repair_note": "Pour ré-appairer : retirez l'accessoire dans Maison, puis re-scannez.",
        "not_paired": "Non appairé — scannez le QR dans l'app Maison",
        "pin": "Code PIN", "hap_port": "Port HAP",
        "raspberry_pi": "Raspberry Pi",
        "hostname": "Hostname", "local_ip": "IP locale", "cpu_temp": "Température CPU",
        "uptime": "Uptime", "load": "Charge", "ram_used": "RAM utilisée",
        "overkiz": "Overkiz / Cozytouch", "state": "État",
        "connected": "connecté", "disconnected": "déconnecté",
        "last_read": "Dernière lecture", "interval": "Intervalle", "last_error": "Dernière erreur",
        "exposed": "Accessoires exposés",
        "c_name": "Nom", "c_type": "Type", "c_value": "Valeur", "c_state": "État",
        "ok": "ok", "unavailable": "indispo", "none": "Aucun accessoire exposé.",
        "footer": "Généré à {t} · rechargez la page pour actualiser",
        "temperature": "température", "humidity": "humidité",
    },
}


def _locale(cfg: dict[str, Any]) -> dict[str, str]:
    lang = str(cfg.get("language", "en")).lower()
    return LOCALES.get(lang, LOCALES["en"])


# ── HTML rendering (pure function) ───────────────────────────────────────────
def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%H:%M:%S") if dt else "—"


def render_html(bridge: Any, cfg: dict[str, Any]) -> str:
    L = _locale(cfg)
    name = html.escape(cfg["homekit"]["name"])
    paired = bool(
        getattr(getattr(bridge, "driver", None), "state", None)
        and bridge.driver.state.paired
    )

    # HomeKit card: QR + PIN always shown, with the pairing status badge.
    pin = "—"
    try:
        pin = bridge.driver.state.pincode.decode()
    except Exception:  # noqa: BLE001
        pass
    qr = ""
    try:
        qr = _qr_svg(bridge.xhm_uri()) or ""
    except Exception:  # noqa: BLE001
        pass
    if paired:
        status_line = (
            f'<p class="ok">{L["paired"]}</p>'
            f'<p class="muted">{L["repair_note"]}</p>'
        )
    else:
        status_line = f'<p class="warn">{L["not_paired"]}</p>'
    homekit_block = (
        status_line
        + f'<div class="qr">{qr}</div>'
        + f'<p>{L["pin"]} : <code>{html.escape(pin)}</code></p>'
    )

    # Overkiz card.
    connected = getattr(bridge, "connected", False)
    last_ok = getattr(bridge, "last_poll_ok", None)
    last_err = getattr(bridge, "last_error", None)
    interval = cfg["polling"]["interval"]
    cz_state = (
        f'<span class="ok">{L["connected"]}</span>' if connected
        else f'<span class="warn">{L["disconnected"]}</span>'
    )
    err_row = (
        f'<tr><td>{L["last_error"]}</td><td>{html.escape(str(last_err))}</td></tr>'
        if last_err else ""
    )

    # Accessories.
    rows = ""
    for comp in getattr(bridge, "_components", []):
        try:
            st = comp.status()
        except Exception:  # noqa: BLE001
            continue
        avail = st.get("available")
        val = st.get("value")
        val_txt = f'{val} {st.get("unit", "")}' if avail and val is not None else "—"
        badge = (
            f'<span class="ok">{L["ok"]}</span>' if avail
            else f'<span class="warn">{L["unavailable"]}</span>'
        )
        kind = L.get(st.get("kind", ""), st.get("kind", ""))
        rows += (
            f"<tr><td>{html.escape(str(st.get('name')))}</td>"
            f"<td>{html.escape(str(kind))}</td>"
            f"<td>{html.escape(val_txt)}</td><td>{badge}</td></tr>"
        )
    if not rows:
        rows = f'<tr><td colspan="4">{L["none"]}</td></tr>'

    cpu = cpu_temp_c()
    return f"""<!doctype html>
<html lang="{L['lang']}"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} — {L['title_suffix']}</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: system-ui, sans-serif; margin: 0; padding: 1rem;
        background:#f4f5f7; color:#1c1e21; }}
 h1 {{ font-size: 1.3rem; margin: .2rem 0 1rem; }}
 .grid {{ display:grid; gap:1rem; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); }}
 .card {{ background:#fff; border-radius:12px; padding:1rem 1.2rem;
         box-shadow:0 1px 3px rgba(0,0,0,.12); }}
 .card h2 {{ font-size:1rem; margin:0 0 .6rem; }}
 table {{ width:100%; border-collapse:collapse; font-size:.9rem; }}
 td {{ padding:.25rem .3rem; border-top:1px solid #eee; }}
 td:first-child {{ color:#666; white-space:nowrap; }}
 .ok {{ color:#1a7f37; font-weight:600; }}
 .warn {{ color:#b35900; font-weight:600; }}
 .muted {{ color:#888; font-size:.8rem; }}
 code {{ background:#eef; padding:.1rem .4rem; border-radius:6px; font-size:1.1rem; }}
 .qr {{ max-width:220px; }} .qr svg {{ width:100%; height:auto; }}
 footer {{ color:#888; font-size:.75rem; margin-top:1rem; }}
 @media (prefers-color-scheme: dark) {{
   body {{ background:#18191a; color:#e4e6eb; }}
   .card {{ background:#242526; box-shadow:none; }}
   td {{ border-top-color:#3a3b3c; }} td:first-child {{ color:#aaa; }}
   code {{ background:#333; }}
 }}
</style></head><body>
<h1>{name}</h1>
<div class="grid">
  <div class="card">
    <h2>{L['homekit_pairing']}</h2>
    {homekit_block}
    <table><tr><td>{L['hap_port']}</td><td>{cfg['homekit']['port']}</td></tr></table>
  </div>
  <div class="card">
    <h2>{L['raspberry_pi']}</h2>
    <table>
      <tr><td>{L['hostname']}</td><td>{html.escape(socket.gethostname())}</td></tr>
      <tr><td>{L['local_ip']}</td><td>{html.escape(local_ip())}</td></tr>
      <tr><td>{L['cpu_temp']}</td><td>{cpu if cpu is not None else '—'} °C</td></tr>
      <tr><td>{L['uptime']}</td><td>{uptime_str() or '—'}</td></tr>
      <tr><td>{L['load']}</td><td>{load_str() or '—'}</td></tr>
      <tr><td>{L['ram_used']}</td><td>{mem_str() or '—'}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>{L['overkiz']}</h2>
    <table>
      <tr><td>{L['state']}</td><td>{cz_state}</td></tr>
      <tr><td>{L['last_read']}</td><td>{_fmt_dt(last_ok)}</td></tr>
      <tr><td>{L['interval']}</td><td>{interval} s</td></tr>
      {err_row}
    </table>
  </div>
  <div class="card" style="grid-column:1/-1">
    <h2>{L['exposed']}</h2>
    <table>
      <tr><td>{L['c_name']}</td><td>{L['c_type']}</td><td>{L['c_value']}</td><td>{L['c_state']}</td></tr>
      {rows}
    </table>
  </div>
</div>
<footer>{L['footer'].format(t=_fmt_dt(datetime.now()))}</footer>
</body></html>"""


# ── aiohttp server ───────────────────────────────────────────────────────────
class StatusServer:
    """Small aiohttp server serving the status page, on the current loop."""

    def __init__(self, bridge: Any, cfg: dict[str, Any]):
        self._bridge = bridge
        self._cfg = cfg
        self._runner: Any = None

    async def start(self) -> None:
        from aiohttp import web

        web_cfg = self._cfg.get("web", {})
        host = web_cfg.get("host", "0.0.0.0")
        port = int(web_cfg.get("port", 8080))

        async def handle_index(_request):
            return web.Response(
                text=render_html(self._bridge, self._cfg), content_type="text/html"
            )

        async def handle_api(_request):
            comps = []
            for comp in getattr(self._bridge, "_components", []):
                try:
                    comps.append(comp.status())
                except Exception:  # noqa: BLE001
                    pass
            return web.json_response({
                "name": self._cfg["homekit"]["name"],
                "paired": bool(self._bridge.driver.state.paired),
                "connected": getattr(self._bridge, "connected", False),
                "last_poll_ok": _fmt_dt(getattr(self._bridge, "last_poll_ok", None)),
                "last_error": getattr(self._bridge, "last_error", None),
                "cpu_temp_c": cpu_temp_c(),
                "uptime": uptime_str(),
                "accessories": comps,
            })

        app = web.Application()
        app.router.add_get("/", handle_index)
        app.router.add_get("/api/status", handle_api)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        _LOGGER.info("Status page: http://%s:%s/", local_ip(), port)

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._runner = None
