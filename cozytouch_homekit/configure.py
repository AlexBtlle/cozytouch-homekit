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
    console.print("\n[bold]2) deviceURL[/] [dim](laisser vide si pas encore fait `explore`)[/]")
    dev = cfg["device"]
    dev["pac_url"] = _ask_text("deviceURL PAC", dev.get("pac_url", ""))
    dev["ecs_url"] = _ask_text("deviceURL ECS (Duo, vide si absent)", dev.get("ecs_url", ""))

    # ── 3. Choix des fonctions ───────────────────────────────────────────────
    console.print("\n[bold]3) Fonctions exposées[/]")
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

    # ── Sauvegarde ───────────────────────────────────────────────────────────
    path = save_config(cfg)
    console.print(f"\n[green]✓[/] Configuration enregistrée : [bold]{path}[/] (droits 0600)")

    active = [n for n in FEATURE_ORDER if cfg["features"].get(n) and n in IMPLEMENTED_FEATURES]
    console.print(
        "Services HomeKit qui seront exposés : "
        + (", ".join(active) if active else "[red]aucun[/]")
    )
    if not dev["pac_url"]:
        console.print(
            "\n[yellow]Pensez à lancer[/] [bold]python -m cozytouch_homekit explore[/] "
            "pour récupérer les deviceURL et confirmer les noms de states."
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
