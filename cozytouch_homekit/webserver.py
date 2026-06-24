"""Mini page web de statut (port 8080 par défaut).

Sert, sur le LAN, un tableau de bord :
  - appairage HomeKit (QR + PIN si non appairé, sinon « appairé ») ;
  - état du Pi (hostname, IP, température CPU, uptime, charge, RAM) ;
  - état Overkiz (connecté, dernière lecture, dernière erreur, intervalle) ;
  - accessoires exposés et leurs valeurs courantes.

Tourne dans le MÊME event loop que le bridge HAP (aiohttp, fourni par
pyoverkiz → pas de dépendance supplémentaire). `render_html()` est une fonction
pure, testable sans serveur.

⚠️ La page affiche le PIN d'appairage et des infos système : à n'exposer que sur
un réseau de confiance (elle n'expose JAMAIS le mot de passe Cozytouch).
"""

from __future__ import annotations

import html
import logging
import os
import socket
from datetime import datetime
from typing import Any

_LOGGER = logging.getLogger(__name__)


# ── Infos système (sans dépendance, cible Linux/Raspberry Pi OS) ─────────────
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
        parts.append(f"{days}j")
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
    return f"{used // 1024} / {total // 1024} Mo"


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
    """SVG inline du QR d'appairage (via pyqrcode). None si indispo."""
    try:
        import io

        import pyqrcode

        buf = io.BytesIO()
        pyqrcode.create(uri).svg(buf, scale=5, quiet_zone=2)
        svg = buf.getvalue().decode("utf-8")
        # Retirer le prologue XML pour un inline propre dans le HTML.
        if svg.startswith("<?xml"):
            svg = svg.split("?>", 1)[-1].lstrip()
        return svg
    except Exception:  # noqa: BLE001 — le QR est un bonus
        return None


# ── Rendu HTML (fonction pure) ───────────────────────────────────────────────
def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%H:%M:%S") if dt else "—"


def render_html(bridge: Any, cfg: dict[str, Any]) -> str:
    name = html.escape(cfg["homekit"]["name"])
    paired = bool(getattr(getattr(bridge, "driver", None), "state", None) and bridge.driver.state.paired)

    # Carte HomeKit : on affiche TOUJOURS le QR + PIN (référence : projet caméra),
    # avec le badge d'état d'appairage.
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
            '<p class="ok">✓ Appairé</p>'
            '<p class="muted">Pour ré-appairer : retirez l\'accessoire dans Maison, '
            'puis re-scannez.</p>'
        )
    else:
        status_line = '<p class="warn">Non appairé — scannez le QR dans l\'app Maison</p>'
    homekit_block = (
        status_line
        + f'<div class="qr">{qr}</div>'
        + f'<p>Code PIN : <code>{html.escape(pin)}</code></p>'
    )

    # Carte Overkiz.
    connected = getattr(bridge, "connected", False)
    last_ok = getattr(bridge, "last_poll_ok", None)
    last_err = getattr(bridge, "last_error", None)
    interval = cfg["polling"]["interval"]
    cz_state = '<span class="ok">connecté</span>' if connected else '<span class="warn">déconnecté</span>'
    err_row = f"<tr><td>Dernière erreur</td><td>{html.escape(str(last_err))}</td></tr>" if last_err else ""

    # Accessoires.
    rows = ""
    for comp in getattr(bridge, "_components", []):
        try:
            st = comp.status()
        except Exception:  # noqa: BLE001
            continue
        avail = st.get("available")
        val = st.get("value")
        val_txt = f'{val} {st.get("unit","")}' if avail and val is not None else "—"
        badge = '<span class="ok">ok</span>' if avail else '<span class="warn">indispo</span>'
        rows += (
            f"<tr><td>{html.escape(str(st.get('name')))}</td>"
            f"<td>{html.escape(str(st.get('kind','')))}</td>"
            f"<td>{html.escape(val_txt)}</td><td>{badge}</td></tr>"
        )
    if not rows:
        rows = '<tr><td colspan="4">Aucun accessoire exposé.</td></tr>'

    return f"""<!doctype html>
<html lang="fr"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>{name} — statut</title>
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
    <h2>Appairage HomeKit</h2>
    {homekit_block}
    <table><tr><td>Port HAP</td><td>{cfg['homekit']['port']}</td></tr></table>
  </div>
  <div class="card">
    <h2>Raspberry Pi</h2>
    <table>
      <tr><td>Hostname</td><td>{html.escape(socket.gethostname())}</td></tr>
      <tr><td>IP locale</td><td>{html.escape(local_ip())}</td></tr>
      <tr><td>Température CPU</td><td>{cpu_temp_c() if cpu_temp_c() is not None else '—'} °C</td></tr>
      <tr><td>Uptime</td><td>{uptime_str() or '—'}</td></tr>
      <tr><td>Charge</td><td>{load_str() or '—'}</td></tr>
      <tr><td>RAM utilisée</td><td>{mem_str() or '—'}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Overkiz / Cozytouch</h2>
    <table>
      <tr><td>État</td><td>{cz_state}</td></tr>
      <tr><td>Dernière lecture</td><td>{_fmt_dt(last_ok)}</td></tr>
      <tr><td>Intervalle</td><td>{interval} s</td></tr>
      {err_row}
    </table>
  </div>
  <div class="card" style="grid-column:1/-1">
    <h2>Accessoires exposés</h2>
    <table>
      <tr><td>Nom</td><td>Type</td><td>Valeur</td><td>État</td></tr>
      {rows}
    </table>
  </div>
</div>
<footer>Rafraîchissement automatique toutes les 10 s · {_fmt_dt(datetime.now())}</footer>
</body></html>"""


# ── Serveur aiohttp ──────────────────────────────────────────────────────────
class StatusServer:
    """Petit serveur aiohttp servant la page de statut, sur la loop courante."""

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
        _LOGGER.info("Page de statut : http://%s:%s/", local_ip(), port)

    async def stop(self) -> None:
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:  # noqa: BLE001
                pass
            self._runner = None
