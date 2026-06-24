"""Point d'entrée CLI : `python -m cozytouch_homekit <commande>`.

Commandes :
    configure   menu interactif (identifiants Cozytouch + choix des fonctions)
    explore     dump des devices/states/commands (Étape 0, découverte)
    run         démarre le service HomeKit (utilisé par systemd)
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 1

    command, rest = argv[0], argv[1:]

    if command in ("configure", "config"):
        from .configure import main as configure_main

        return configure_main(rest)
    if command == "explore":
        from .explore import main as explore_main

        return explore_main(rest)
    if command == "run":
        from .runner import main as run_main

        return run_main(rest)
    if command in ("-h", "--help", "help"):
        print(__doc__)
        return 0

    print(f"Commande inconnue : {command!r}\n")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
