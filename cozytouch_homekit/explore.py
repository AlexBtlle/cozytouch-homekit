"""Étape 0 — Découverte de l'API Overkiz AVANT tout mapping.

Dumpe tous les devices, leurs states (avec valeurs) et leurs commands dans un
JSON de référence. Objectif : figer la liste EXACTE des states de votre
firmware (noms, unités, plages) au lieu de coder le mapping en aveugle.

Usage :
    python -m cozytouch_homekit explore                  # dump complet
    python -m cozytouch_homekit explore --anonymize      # masque les IDs passerelle
    python -m cozytouch_homekit explore --out dump.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .config import PROJECT_ROOT, load_config
from .overkiz_client import CozytouchClient

console = Console()

# io://1234-5678-9012/87654321  → on masque les blocs numériques de l'ID passerelle.
_GATEWAY_RE = re.compile(r"(\d{4})-(\d{4})-(\d{4})")


def _anonymize(text: str) -> str:
    return _GATEWAY_RE.sub("XXXX-XXXX-XXXX", text)


def _device_to_dict(device: Any, anonymize: bool) -> dict[str, Any]:
    url = str(getattr(device, "device_url", ""))
    label = str(getattr(device, "label", ""))
    if anonymize:
        url = _anonymize(url)

    states = []
    for state in getattr(device, "states", []) or []:
        states.append(
            {
                "name": getattr(state, "name", None),
                "value": getattr(state, "value", None),
                "type": getattr(state, "type", None),
            }
        )

    commands = []
    definition = getattr(device, "definition", None)
    for cmd in getattr(definition, "commands", []) or []:
        commands.append(
            {
                "name": getattr(cmd, "command_name", getattr(cmd, "name", None)),
                "nparams": getattr(cmd, "nparams", None),
            }
        )

    return {
        "device_url": url,
        "label": label,
        "controllable_name": str(getattr(device, "controllable_name", "")),
        "widget": str(getattr(device, "widget", "")),
        "ui_class": str(getattr(device, "ui_class", "")),
        "states": states,
        "commands": commands,
    }


async def _run(anonymize: bool, out_path: Path) -> int:
    cfg = load_config()
    creds = cfg["cozytouch"]
    if not creds.get("username") or not creds.get("password"):
        console.print("[red]Identifiants Cozytouch manquants.[/] Lancez `configure`.")
        return 1

    client = CozytouchClient(creds["username"], creds["password"], creds["server"])
    try:
        console.print("[cyan]Connexion au cloud Overkiz…[/]")
        devices = await client.get_devices()
    finally:
        await client.close()

    dump = [_device_to_dict(d, anonymize) for d in devices]

    out_path.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]✓[/] Dump écrit : [bold]{out_path}[/] ({len(dump)} devices)")
    if anonymize:
        console.print("[dim]IDs passerelle masqués (XXXX-XXXX-XXXX).[/]")
    else:
        console.print(
            "[yellow]⚠ Dump NON anonymisé[/] — ne le commitez pas tel quel "
            "(il contient l'ID de votre passerelle). Voir .gitignore."
        )

    # Résumé console : table device → URL → nb states.
    table = Table(title="Devices découverts")
    table.add_column("Label", style="bold")
    table.add_column("controllable_name")
    table.add_column("device_url")
    table.add_column("#states", justify="right")
    for d in dump:
        table.add_row(
            d["label"], d["controllable_name"], d["device_url"], str(len(d["states"]))
        )
    console.print(table)

    console.print(
        "\n[bold]Prochaine étape :[/] reportez les `device_url` (PAC / ECS) et les "
        "noms de states de température dans `config.yaml` via `configure`, ou "
        "éditez directement les sections [cyan]device[/] et [cyan]sensors[/]."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="explore", description="Découverte API Overkiz")
    parser.add_argument(
        "--anonymize", action="store_true", help="masquer les IDs de passerelle"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "explore_dump.json",
        help="fichier de sortie JSON",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.anonymize, args.out))
