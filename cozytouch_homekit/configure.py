"""Menu console `configure` : identifiants Cozytouch + détection des fonctions.

Re-lançable à tout moment. Écrit config.yaml (gitignored).

Flux :
  1. saisie des identifiants Cozytouch ;
  2. connexion au compte → **détection automatique** des capacités exposables
     (capteurs de température/humidité, consignes…) ;
  3. l'utilisateur **coche** ce qu'il veut exposer à HomeKit ;
  4. chaque capacité choisie devient un accessoire du bridge (AID stable).

Utilise questionary si disponible, sinon repli sur input()/getpass.
"""

from __future__ import annotations

import getpass
from typing import Any

from rich.console import Console

from .config import DEFAULT_CONFIG, assign_aids, load_config, resolve_password, save_config
from .detect import detect_capabilities
from .secret_store import encrypt, is_encrypted

console = Console()

try:
    import questionary

    _HAS_QUESTIONARY = True
except Exception:  # pragma: no cover - repli si non installé
    _HAS_QUESTIONARY = False


def _load_or_default() -> dict[str, Any]:
    try:
        return load_config()
    except FileNotFoundError:
        import copy

        return copy.deepcopy(DEFAULT_CONFIG)


def _ask_text(prompt: str, default: str = "") -> str:
    if _HAS_QUESTIONARY:
        return questionary.text(prompt, default=default).ask() or default
    raw = input(f"{prompt} [{default}]: ").strip()
    return raw or default


def _ask_password(prompt: str, has_existing: bool) -> str | None:
    """Renvoie None pour « conserver l'actuel »."""
    suffix = " (laisser vide pour conserver l'actuel)" if has_existing else ""
    if _HAS_QUESTIONARY:
        value = questionary.password(prompt + suffix).ask()
    else:
        value = getpass.getpass(f"{prompt}{suffix}: ")
    value = (value or "").strip()
    if not value and has_existing:
        return None
    return value


def _discover_devices(cfg: dict[str, Any]) -> list[Any]:
    """Récupère les devices Overkiz du compte. [] si échec/identifiants vides."""
    import asyncio

    from .overkiz_client import CozytouchClient

    cz = cfg["cozytouch"]
    if not cz.get("username") or not cz.get("password"):
        return []

    async def _run() -> list[Any]:
        client = CozytouchClient(cz["username"], resolve_password(cfg), cz["server"])
        try:
            return await client.get_devices()
        finally:
            await client.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Connexion Overkiz échouée :[/] {exc}")
        return []


def _cap_key(device_url: str, state: str) -> str:
    return f"{device_url}::{state}"


def _select_capabilities(caps: list[Any], previous: list[dict[str, Any]]) -> list[Any]:
    """Checkbox sur les capacités détectées. Renvoie les Capability cochées."""
    prev_keys = {k for e in previous if (k := _entry_key(e))}

    console.print(
        f"\n[green]✓[/] {len(caps)} capacité(s) détectée(s) sur ton compte."
    )
    def _line(c):
        extra = f"  ({c.summary})" if c.summary else ""
        return f"[{c.category}] {c.name}{extra}"

    if _HAS_QUESTIONARY:
        choices = [
            questionary.Choice(title=_line(c), value=c, checked=c.key in prev_keys)
            for c in caps
        ]
        chosen = questionary.checkbox(
            "Capacités à exposer à HomeKit (espace = cocher, entrée = valider) :",
            choices=choices,
        ).ask()
        return chosen or []

    # Repli texte.
    console.print("Capacités détectées (o/n) :")
    chosen = []
    for c in caps:
        default = "o" if c.key in prev_keys else "n"
        ans = input(f"  {_line(c)} [{default}]: ").strip().lower() or default
        if ans.startswith("o") or ans.startswith("y"):
            chosen.append(c)
    return chosen


def _entry_key(e: dict[str, Any]) -> str | None:
    """Clé stable d'une entrée exposed (gère l'ancien format avec `state`)."""
    if e.get("key"):
        return e["key"]
    st = (e.get("spec") or {}).get("state") or e.get("state")
    return _cap_key(e.get("device_url", ""), st) if st else None


def _capabilities_to_exposed(
    caps: list[Any], previous: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Construit la liste `exposed`, en conservant AID et nom déjà attribués."""
    prev_by_key: dict[str, dict[str, Any]] = {}
    for e in previous:
        k = _entry_key(e)
        if k:
            prev_by_key[k] = e
    exposed: list[dict[str, Any]] = []
    for c in caps:
        prev = prev_by_key.get(c.key)
        exposed.append({
            "key": c.key,
            "aid": prev.get("aid") if prev else None,
            "type": c.type,
            "name": prev.get("name") if prev else c.name,
            "device_url": c.device_url,
            "spec": c.spec,
        })
    assign_aids(exposed)
    return exposed


def run_configure(argv: list[str] | None = None) -> int:
    console.print("[bold cyan]Configuration cozytouch-homekit[/]\n")
    cfg = _load_or_default()

    # ── 1. Identifiants Cozytouch ────────────────────────────────────────────
    console.print("[bold]1) Identifiants Cozytouch[/]")
    cz = cfg["cozytouch"]
    cz["username"] = _ask_text("Email / login Cozytouch", cz.get("username", ""))
    new_pw = _ask_password("Mot de passe Cozytouch", bool(cz.get("password")))
    if new_pw is not None:
        cz["password"] = new_pw
    cz["server"] = _ask_text("Serveur Overkiz", cz.get("server", "atlantic_cozytouch"))

    # ── 2. Détection + choix des fonctions ───────────────────────────────────
    console.print("\n[bold]2) Détection des fonctions Overkiz[/]")
    console.print("[cyan]Connexion au compte Overkiz…[/]")
    devices = _discover_devices(cfg)

    if not devices:
        console.print(
            "[yellow]Aucun device récupéré[/] (identifiants faux, hors-ligne, ou "
            "rate-limit). La sélection existante est conservée."
        )
    else:
        caps = detect_capabilities(devices)
        if not caps:
            console.print(
                "[yellow]Aucune capacité exposable détectée[/] sur tes devices."
            )
        else:
            chosen = _select_capabilities(caps, cfg.get("exposed", []) or [])
            cfg["exposed"] = _capabilities_to_exposed(chosen, cfg.get("exposed", []) or [])

    # ── Chiffrement du mot de passe au repos ─────────────────────────────────
    pw = cfg["cozytouch"].get("password", "") or ""
    if pw and not is_encrypted(pw):
        cfg["cozytouch"]["password"] = encrypt(pw)
        console.print("[dim]Mot de passe chiffré dans config.yaml (clé : .secret.key).[/]")

    # ── Sauvegarde ───────────────────────────────────────────────────────────
    path = save_config(cfg)
    console.print(f"\n[green]✓[/] Configuration enregistrée : [bold]{path}[/] (droits 0600)")

    exposed = cfg.get("exposed", []) or []
    if exposed:
        console.print("Accessoires HomeKit qui seront exposés :")
        for e in exposed:
            console.print(f"   • AID {e['aid']} — [bold]{e['name']}[/] ({e['type']})")
    else:
        console.print("[red]Aucun accessoire exposé.[/] Relance `configure` connecté.")

    console.print(
        "\n[dim]Note : ajouter/retirer un accessoire APRÈS appairage nécessite de "
        "redémarrer le service (config number c# incrémenté → HomeKit relit). "
        "Ajout = propre ; retrait = possible tuile fantôme.[/]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run_configure(argv)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[red]Annulé.[/]")
        return 1
