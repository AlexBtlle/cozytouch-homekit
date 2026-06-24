"""Menu console `configure` : identifiants Cozytouch + choix des fonctions.

Re-lançable à tout moment. Écrit config.yaml (gitignored).
Utilise questionary si disponible, sinon repli sur input()/getpass.
"""

from __future__ import annotations

import getpass
from typing import Any

from rich.console import Console

from .config import (
    CONFIG_PATH,
    DEFAULT_CONFIG,
    FEATURE_ORDER,
    IMPLEMENTED_FEATURES,
    load_config,
    save_config,
)

console = Console()

try:
    import questionary

    _HAS_QUESTIONARY = True
except Exception:  # pragma: no cover - repli si non installé
    _HAS_QUESTIONARY = False


# Libellés lisibles pour les features.
FEATURE_LABELS = {
    "temp_ambiante": "Capteur température ambiante",
    "temp_exterieure": "Capteur température extérieure",
    "temp_ecs": "Capteur température ECS (ballon, Duo)",
    "thermostat": "Thermostat / contrôle chauffage  [V2 — non implémenté]",
    "boost_ecs": "Boost ECS  [V2 — non implémenté]",
}


def _load_or_default() -> dict[str, Any]:
    try:
        return load_config()
    except FileNotFoundError:
        # Copie profonde des défauts.
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


def _ask_features(current: dict[str, bool]) -> dict[str, bool]:
    selectable = list(FEATURE_ORDER)
    if _HAS_QUESTIONARY:
        choices = [
            questionary.Choice(
                title=FEATURE_LABELS[name],
                value=name,
                checked=bool(current.get(name)),
                # On laisse cochables les V2 mais on avertira ensuite.
            )
            for name in selectable
        ]
        chosen = questionary.checkbox(
            "Fonctions à exposer à HomeKit (espace = cocher, entrée = valider) :",
            choices=choices,
        ).ask()
        chosen = chosen or []
        return {name: (name in chosen) for name in selectable}

    # Repli texte.
    console.print("\nFonctions à exposer (o/n) :")
    result = {}
    for name in selectable:
        default = "o" if current.get(name) else "n"
        ans = input(f"  {FEATURE_LABELS[name]} [{default}]: ").strip().lower() or default
        result[name] = ans.startswith("o") or ans.startswith("y")
    return result


def _discover_devices(cfg: dict[str, Any]) -> list[Any]:
    """Liste les devices Overkiz (pour aider au mapping). [] si échec."""
    import asyncio

    from .overkiz_client import CozytouchClient

    cz = cfg["cozytouch"]
    if not cz.get("username") or not cz.get("password"):
        return []

    async def _run() -> list[Any]:
        client = CozytouchClient(cz["username"], cz["password"], cz["server"])
        try:
            return await client.get_devices()
        finally:
            await client.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — la découverte est un bonus
        console.print(f"[yellow]Découverte impossible ({exc}). Saisie manuelle.[/]")
        return []


def _print_device_reference(devices: list[Any]) -> None:
    """Affiche label / controllable_name / device_url + states de température."""
    from rich.table import Table

    table = Table(title="Devices avec states de température")
    table.add_column("Label", style="bold")
    table.add_column("controllable_name")
    table.add_column("device_url", style="cyan")
    table.add_column("states température (valeur)")
    for dev in devices:
        temps = [
            f"{getattr(s, 'name', '')}={getattr(s, 'value', '')}"
            for s in getattr(dev, "states", []) or []
            if "emperatur" in str(getattr(s, "name", ""))
        ]
        if not temps:
            continue
        table.add_row(
            str(getattr(dev, "label", "")),
            str(getattr(dev, "controllable_name", "")),
            str(getattr(dev, "device_url", "")),
            "\n".join(temps),
        )
    console.print(table)


def _configure_sensor_mapping(cfg: dict[str, Any], active: list[str]) -> None:
    """Pour chaque capteur activé : saisie device_url + state Overkiz."""
    sensors = cfg.setdefault("sensors", {})

    # Aide optionnelle : récupérer la liste des devices depuis Overkiz.
    want_discovery = _ask_text(
        "Récupérer la liste des devices depuis Overkiz pour t'aider ? (o/n)", "o"
    ).strip().lower().startswith("o")
    if want_discovery:
        console.print("[cyan]Connexion à Overkiz…[/]")
        devices = _discover_devices(cfg)
        if devices:
            _print_device_reference(devices)
            console.print(
                "[dim]Copie le device_url de la sonde voulue ci-dessus.[/]"
            )

    for feature in active:
        label = FEATURE_LABELS.get(feature, feature).split("  [")[0]
        console.print(f"\n[bold]{label}[/]")
        current = sensors.get(feature, {})
        device_url = _ask_text("  device_url", current.get("device_url", ""))
        state = _ask_text(
            "  state Overkiz", current.get("state", "core:TemperatureState")
        )
        sensors[feature] = {"device_url": device_url.strip(), "state": state.strip()}


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
    cz["server"] = _ask_text(
        "Serveur Overkiz", cz.get("server", "atlantic_cozytouch")
    )

    # ── 2. deviceURL (optionnel ici, sinon via `explore`) ────────────────────
    # ── 2. Choix des fonctions ───────────────────────────────────────────────
    console.print("\n[bold]2) Fonctions exposées[/]")
    cfg["features"] = _ask_features(cfg.get("features", {}))

    # Avertissement V2.
    v2_on = [
        n for n in cfg["features"]
        if cfg["features"][n] and n not in IMPLEMENTED_FEATURES
    ]
    if v2_on:
        console.print(
            "[yellow]⚠ Sélectionné mais non implémenté en V1 (ignoré au démarrage) :[/] "
            + ", ".join(FEATURE_LABELS[n].split("  [")[0] for n in v2_on)
        )

    # ── 3. Mapping des capteurs activés (device_url + state Overkiz) ──────────
    active = [n for n in FEATURE_ORDER if cfg["features"].get(n) and n in IMPLEMENTED_FEATURES]
    if active:
        console.print("\n[bold]3) Mapping des capteurs[/] [dim](device_url + state Overkiz)[/]")
        _configure_sensor_mapping(cfg, active)
    else:
        console.print("\n[dim]Aucun capteur de température activé à mapper.[/]")

    # ── Sauvegarde ───────────────────────────────────────────────────────────
    path = save_config(cfg)
    console.print(f"\n[green]✓[/] Configuration enregistrée : [bold]{path}[/] (droits 0600)")

    console.print(
        "Services HomeKit qui seront exposés : "
        + (", ".join(active) if active else "[red]aucun[/]")
    )
    unmapped = [n for n in active if not cfg["sensors"].get(n, {}).get("device_url")]
    if unmapped:
        console.print(
            "\n[yellow]⚠ Sans device_url, ces capteurs ne seront pas créés :[/] "
            + ", ".join(unmapped)
            + "\n  Lancez [bold]python -m cozytouch_homekit explore[/] puis re-`configure`."
        )
    console.print(
        "\n[dim]Note : modifier la structure APRÈS appairage nécessite de "
        "redémarrer le service (config number c# incrémenté → HomeKit relit).[/]"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run_configure(argv)
    except (KeyboardInterrupt, EOFError):
        console.print("\n[red]Annulé.[/]")
        return 1
