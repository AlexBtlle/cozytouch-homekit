"""Wrapper async autour de pyoverkiz : login, refresh session, lecture states.

Gère le rate-limit Overkiz (cloud-only) : c'est l'appelant qui espace les
appels et applique le backoff, mais on expose des exceptions claires pour
qu'il sache distinguer « réessaie plus tard » de « identifiants faux ».
"""

from __future__ import annotations

import logging
from typing import Any

from pyoverkiz.client import OverkizClient
from pyoverkiz.const import SUPPORTED_SERVERS
from pyoverkiz.exceptions import (
    BadCredentialsException,
    MaintenanceException,
    NotAuthenticatedException,
    TooManyRequestsException,
)

_LOGGER = logging.getLogger(__name__)


class AuthError(Exception):
    """Identifiants invalides — inutile de réessayer sans corriger la config."""


class TransientError(Exception):
    """Erreur temporaire (rate-limit, maintenance, réseau) — réessayer + backoff."""


class CozytouchClient:
    """Connexion Overkiz réutilisable. À instancier DANS l'event loop HAP."""

    def __init__(self, username: str, password: str, server: str = "atlantic_cozytouch"):
        if server not in SUPPORTED_SERVERS:
            raise ValueError(
                f"Serveur Overkiz inconnu : {server!r}. "
                f"Valeurs possibles : {', '.join(sorted(SUPPORTED_SERVERS))}"
            )
        self._username = username
        self._password = password
        self._server = SUPPORTED_SERVERS[server]
        self._client: OverkizClient | None = None
        self._logged_in = False

    async def _ensure_client(self) -> OverkizClient:
        if self._client is None:
            # pyoverkiz crée sa propre ClientSession aiohttp dans la loop courante.
            self._client = OverkizClient(
                username=self._username,
                password=self._password,
                server=self._server,
            )
        return self._client

    async def login(self) -> None:
        """(Re)authentifie. Lève AuthError / TransientError."""
        client = await self._ensure_client()
        try:
            await client.login()
            self._logged_in = True
            _LOGGER.info("Connecté au cloud Overkiz (%s).", self._server.name)
        except BadCredentialsException as exc:
            self._logged_in = False
            raise AuthError(f"Identifiants Cozytouch refusés : {exc}") from exc
        except (TooManyRequestsException, MaintenanceException) as exc:
            self._logged_in = False
            raise TransientError(f"Overkiz indisponible (rate-limit/maintenance) : {exc}") from exc
        except Exception as exc:  # réseau, DNS, etc.
            self._logged_in = False
            raise TransientError(f"Échec de connexion Overkiz : {exc}") from exc

    async def _ensure_login(self) -> OverkizClient:
        client = await self._ensure_client()
        if not self._logged_in:
            await self.login()
        return client

    async def get_devices(self) -> list[Any]:
        """Liste brute des devices (utilisé par `explore`)."""
        client = await self._ensure_login()
        try:
            return await client.get_devices()
        except NotAuthenticatedException:
            self._logged_in = False
            await self.login()
            return await client.get_devices()
        except (TooManyRequestsException, MaintenanceException) as exc:
            raise TransientError(f"get_devices throttlé : {exc}") from exc

    async def read_states(self, device_url: str) -> dict[str, Any]:
        """Renvoie {nom_state: valeur} pour un device. Réauth auto si session expirée."""
        client = await self._ensure_login()
        try:
            states = await client.get_state(device_url)
        except NotAuthenticatedException:
            _LOGGER.info("Session Overkiz expirée → reconnexion.")
            self._logged_in = False
            await self.login()
            states = await client.get_state(device_url)
        except (TooManyRequestsException, MaintenanceException) as exc:
            raise TransientError(f"Rate-limit/maintenance sur {device_url} : {exc}") from exc
        except Exception as exc:
            raise TransientError(f"Lecture states échouée ({device_url}) : {exc}") from exc

        # pyoverkiz peut renvoyer une liste de State ou un objet States itérable.
        result: dict[str, Any] = {}
        for state in states or []:
            name = getattr(state, "name", None)
            if name is not None:
                result[name] = getattr(state, "value", None)
        return result

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:  # noqa: BLE001 — best-effort au shutdown
                pass
            self._client = None
            self._logged_in = False
