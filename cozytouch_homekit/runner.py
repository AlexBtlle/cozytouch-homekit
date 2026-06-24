"""Démarrage du service : AccessoryDriver + bridge + QR code.

Au 1er démarrage (non appairé), affiche le QR code d'appairage HAP dans la
console (URI X-HM://, généré nativement par HAP-python).
"""

from __future__ import annotations

import logging
import signal
import sys

from pyhap.accessory_driver import AccessoryDriver

from .accessory import CozytouchBridge
from .config import load_config, resolve_persist_path

_LOGGER = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _print_pairing(accessory: CozytouchBridge, driver: AccessoryDriver) -> None:
    """Affiche le QR code / code d'appairage si l'accessoire n'est pas appairé."""
    if driver.state.paired:
        _LOGGER.info("Accessoire déjà appairé — pas de QR code à afficher.")
        return

    print("\n" + "=" * 60)
    print("  APPAIRAGE HOMEKIT — scannez ce QR code dans l'app Maison")
    print("=" * 60)
    try:
        # setup_message() imprime le pincode + le QR (via pyqrcode si installé).
        accessory.setup_message()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Impossible d'afficher le QR (%s). Repli sur l'URI/pincode.", exc)
        try:
            print("URI d'appairage :", accessory.xhm_uri())
        except Exception:
            pass
        print("Code d'appairage (pincode) :", driver.state.pincode.decode())
    print("=" * 60)
    print("  Pi Zero W (ARMv6) : l'appairage peut prendre PLUSIEURS MINUTES")
    print("  (handshake crypto sur single-core). Ce n'est pas un plantage.")
    print("=" * 60 + "\n")


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    cfg = load_config()

    if not cfg["cozytouch"].get("username") or not cfg["cozytouch"].get("password"):
        _LOGGER.error("Identifiants Cozytouch manquants. Lancez `configure`.")
        return 1

    persist = resolve_persist_path(cfg)
    driver = AccessoryDriver(
        port=int(cfg["homekit"]["port"]),
        persist_file=str(persist),
    )

    bridge = CozytouchBridge(driver, cfg)
    driver.add_accessory(accessory=bridge)

    _print_pairing(bridge, driver)

    # Arrêt propre sur SIGTERM (systemd) et SIGINT.
    signal.signal(signal.SIGTERM, driver.signal_handler)
    signal.signal(signal.SIGINT, driver.signal_handler)

    _LOGGER.info("Démarrage du driver HAP sur le port %s…", cfg["homekit"]["port"])
    driver.start()  # bloquant jusqu'au signal d'arrêt
    return 0


if __name__ == "__main__":
    sys.exit(main())
