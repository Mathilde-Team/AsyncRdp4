# SPDX-License-Identifier: GPL-3.0-or-later
"""
gcm_gtk4_rdp_session.py

Assemble un Client asyncrdp connecté avec les deux ponts GTK4 existants
(RdpView pour l'affichage/les entrées, ClipboardBridge pour le
presse-papier) et porte leur cycle de vie commun : une instance de
RdpSession = une session RDP complète dans un futur onglet GCM.

Jusqu'ici chaque pont était instancié et vérifié séparément (voir
tests/test_gtk4_live.py) — jamais assemblés dans un seul objet
représentant réellement "un onglet RDP" de bout en bout, connexion
comprise. C'est ce morceau qui manquait (voir CLAUDE.md, « Prochaine
étape », « Consolider le plugin RDP de GCM »).

Volontairement séparé de gcm_gtk4_demo_viewer.py (qui construit la
vraie fenêtre GTK4/Gtk.Application) : cette classe ne dépend que d'un
ClipboardBridge déjà importable, d'une RdpView déjà attachable, et d'un
callable de connexion injecté (même signature que asyncrdp.connect,
jamais appelée en dur ici) — aucune dépendance directe à
Gtk.Application/ApplicationWindow. Testable en logique pure avec
tests/gtk_stub/ (voir tests/test_gtk4_demo_viewer.py), sans display ni
serveur RDP réels, dans le même esprit que les deux ponts eux-mêmes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gcm_gtk4_clipboard_bridge import ClipboardBridge
from loguru import logger

from asyncrdp.tracing import traced

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from gcm_gtk4_display_bridge import RdpView
    from gi.repository import Gdk

    from asyncrdp import Client as RdpClient
    from asyncrdp import RdpOptions


class RdpSession:
    """Une session RDP assemblée : connexion asyncrdp + RdpView (affichage/
    entrées) + ClipboardBridge (presse-papier), avec un cycle de vie commun
    (connect()/disconnect()) plutôt que trois objets gérés séparément par
    l'appelant.

    `connect_fn` reçoit exactement les arguments de connect() ci-dessous et
    doit renvoyer un gestionnaire de contexte asynchrone cédant un Client
    (même contrat que asyncrdp.connect) — dans l'appli réelle c'est
    asyncrdp.connect lui-même (voir gcm_gtk4_demo_viewer.py) ; dans les
    tests de logique pure, une doublure sans réseau ni FreeRDP.
    """

    @traced
    def __init__(
        self,
        *,
        view: RdpView,
        gdk_display: Gdk.Display,
        connect_fn: Callable[..., AbstractAsyncContextManager[RdpClient]],
    ) -> None:
        self._view = view
        self._gdk_display = gdk_display
        self._connect_fn = connect_fn
        self._client_ctx: AbstractAsyncContextManager[RdpClient] | None = None
        self._client: RdpClient | None = None
        self._clipboard_bridge: ClipboardBridge | None = None

    @property
    def client(self) -> RdpClient | None:
        return self._client

    @property
    def connected(self) -> bool:
        return self._client is not None

    @traced
    async def connect(
        self,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        *,
        domain: str | None = None,
        options: RdpOptions | None = None,
    ) -> RdpClient:
        """Connecte via connect_fn, puis assemble RdpView + ClipboardBridge
        sur le Client obtenu. En cas d'échec de l'assemblage APRÈS une
        connexion réussie (attach()/ClipboardBridge() lève), referme la
        connexion plutôt que de la laisser fuiter — sans ce filet, une
        exception dans l'un des deux ponts laisserait une session FreeRDP
        connectée mais orpheline, sans qu'aucun appelant n'en garde la
        référence pour la refermer."""
        if self._client is not None:
            raise RuntimeError("RdpSession.connect() appelé alors qu'une session est déjà active")

        logger.debug("RdpSession: connexion à {}:{}...", host, port)
        client_ctx = self._connect_fn(host, port, username, password, domain=domain, options=options)
        client = await client_ctx.__aenter__()

        try:
            self._view.attach(client)
            self._clipboard_bridge = ClipboardBridge(client, self._gdk_display)
        except Exception:
            logger.exception("RdpSession: échec de l'assemblage après connexion, fermeture de la connexion")
            self._view.detach()
            self._clipboard_bridge = None
            await client_ctx.__aexit__(None, None, None)
            raise

        self._client_ctx = client_ctx
        self._client = client
        logger.info("RdpSession: connectée et assemblée (affichage + presse-papier)")
        return client

    @traced
    async def disconnect(self) -> None:
        """Défait l'assemblage puis referme la connexion. Idempotente et
        sûre même sans connexion active (ex: fenêtre fermée avant la fin
        de connect()) : chaque étape vérifie son propre état plutôt que de
        supposer que les précédentes ont eu lieu."""
        self._view.detach()
        if self._clipboard_bridge is not None:
            self._clipboard_bridge.close()
            self._clipboard_bridge = None
        if self._client_ctx is not None:
            client_ctx = self._client_ctx
            self._client_ctx = None
            self._client = None
            await client_ctx.__aexit__(None, None, None)
            logger.info("RdpSession: déconnectée")
