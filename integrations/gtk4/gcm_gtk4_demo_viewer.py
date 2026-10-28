#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
gcm_gtk4_demo_viewer.py

Application GTK4 minimale, réellement exécutable, qui assemble les deux
ponts (gcm_gtk4_display_bridge.RdpView, gcm_gtk4_clipboard_bridge.
ClipboardBridge) dans une vraie fenêtre — via RdpSession
(gcm_gtk4_rdp_session.py, qui porte le cycle de vie connecté/assemblé).

Jusqu'ici chaque pont était validé séparément contre un display/serveur
réels (voir tests/test_gtk4_live.py) mais jamais réunis dans une seule
application : c'est le candidat resté ouvert dans CLAUDE.md
(« Consolider le plugin RDP de GCM »). Ce fichier N'EST PAS le plugin
GCM lui-même — l'architecture à plugins de GCM (ConnectionPlugin et
consorts) n'existe pas dans ce dépôt, seule la bibliothèque asyncrdp y
vit. C'est un visualiseur de référence autonome, pensé comme point de
départ direct de ce plugin une fois GCM disponible comme application
hôte : même assemblage (RdpView + ClipboardBridge sur un Client asyncrdp
connecté, via RdpSession), sans le cadre de plugin GCM autour.

Usage :
    python3 gcm_gtk4_demo_viewer.py <host> <username> <password>
        [--port 3389] [--domain CORP] [--width 1280] [--height 800]
        [--no-clipboard] [--drive nom:/chemin/local ...]
        [--ignore-certificate] [--log-level INFO]

Intègre asyncio à la boucle GLib de GTK4 (gcm_gtk4_asyncio : gi.events, gbulb en repli) — même recette que
tests/test_gtk4_live.py::test_clipboard_bridge_files_download_from_rdp_via_external_reader
(seul endroit de ce dépôt où elle avait été vérifiée en conditions
réelles jusqu'ici, pour la durée d'un seul test — ici pour la durée de
toute l'application).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

import gi
from loguru import logger

# gi.require_version() doit être appelé avant le tout premier
# `from gi.repository import ...` du process pour cette version-là (voir
# gcm_gtk4_clipboard_bridge._load_native_image_provider() pour le détail de
# cette contrainte GObject-Introspection) — c'est pour ça que ni
# gcm_gtk4_display_bridge.py ni gcm_gtk4_clipboard_bridge.py ne l'appellent
# eux-mêmes : ce fichier, en tant qu'application hôte, en est responsable,
# exactement comme le sera GCM lui-même plus tard. `hasattr` garde ce
# module important sous tests/gtk_stub/ (dont le faux `gi` n'a pas
# `require_version`, voir tests/test_gtk4_demo_viewer.py) — même garde que
# celle déjà utilisée dans gcm_gtk4_clipboard_bridge.py.
if hasattr(gi, "require_version"):
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")

from gcm_gtk4_display_bridge import RdpView
from gcm_gtk4_rdp_session import RdpSession
from gi.repository import Gdk, Gtk

import asyncrdp
from asyncrdp.tracing import traced

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from asyncrdp import Client as RdpClient


@traced
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument("--port", type=int, default=3389)
    p.add_argument("--domain", default=None, help="Domaine Active Directory (CORP\\utilisateur)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument(
        "--no-clipboard", action="store_true", help="Désactive la redirection presse-papier (activée par défaut)"
    )
    p.add_argument(
        "--drive",
        action="append",
        default=[],
        metavar="NOM:CHEMIN",
        help="Redirige un dossier local sous ce nom côté serveur ; répétable",
    )
    p.add_argument(
        "--ignore-certificate",
        action="store_true",
        help="Ignore la vérification du certificat serveur (dev/lab uniquement, jamais en production)",
    )
    p.add_argument("--log-level", default="INFO", choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


@traced
def build_rdp_options(args: argparse.Namespace) -> asyncrdp.RdpOptions:
    """Traduit les arguments CLI en RdpOptions — séparé de parse_args() pour
    rester testable sans réanalyser une ligne de commande (voir
    tests/test_gtk4_demo_viewer.py). Lève ValueError sur une entrée
    --drive malformée plutôt que de la rediriger silencieusement sous un
    nom tronqué ou vide."""
    drives = []
    for entry in args.drive:
        name, sep, path = entry.partition(":")
        if not sep or not name or not path:
            raise ValueError(f"--drive attend NOM:CHEMIN (les deux non vides), reçu {entry!r}")
        drives.append(asyncrdp.DriveMapping(name, path))

    return asyncrdp.RdpOptions(
        width=args.width,
        height=args.height,
        redirect_clipboard=not args.no_clipboard,
        redirect_drives=bool(drives),
        drives=drives,
        ignore_certificate=args.ignore_certificate,
    )


class DemoApp(Gtk.Application):
    """Une fenêtre = une session RDP assemblée (RdpSession). Volontairement
    minimal : pas de dialogue de connexion (les paramètres arrivent en CLI,
    cf. parse_args) — le seul sujet de ce fichier est l'assemblage
    affichage+entrées+presse-papier+connexion dans une vraie appli GTK4,
    pas une UI de saisie que GCM fournira de toute façon lui-même."""

    @traced
    def __init__(
        self,
        args: argparse.Namespace,
        options: asyncrdp.RdpOptions,
        connect_fn: Callable[..., AbstractAsyncContextManager[RdpClient]] | None = None,
    ) -> None:
        super().__init__(application_id="org.asyncrdp.demo.viewer")
        self._args = args
        self._options = options
        self._connect_fn = connect_fn if connect_fn is not None else asyncrdp.connect
        self._session: RdpSession | None = None
        self._shutting_down = False
        # Références fortes sur les tâches fire-and-forget créées par
        # _spawn() ci-dessous : sans ça, l'event loop ne garde qu'une
        # référence faible et peut les garbage-collecter en plein vol —
        # même bug (et même correctif) que celui trouvé et corrigé côté
        # _core.py/ClipboardBridge le 2026-09-09 (voir CLAUDE.md).
        self._background_tasks: set[asyncio.Task] = set()
        self.connect("activate", self._on_activate)

    @traced
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    @traced
    def _on_activate(self, app: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=app)
        window.set_title(f"asyncrdp — {self._args.username}@{self._args.host}")
        window.set_default_size(self._args.width, self._args.height)

        view = RdpView()
        window.set_child(view)
        window.connect("close-request", self._on_close_request)
        window.present()

        self._session = RdpSession(
            view=view,
            gdk_display=Gdk.Display.get_default(),
            connect_fn=self._connect_fn,
        )
        self._spawn(self._connect(window))

    @traced
    async def _connect(self, window: Gtk.ApplicationWindow) -> None:
        assert self._session is not None
        try:
            await self._session.connect(
                self._args.host,
                self._args.port,
                self._args.username,
                self._args.password,
                domain=self._args.domain,
                options=self._options,
            )
        except asyncrdp.FreeRDPError as exc:
            logger.error("Échec de connexion : {}", exc)
            window.set_title(f"asyncrdp — échec de connexion ({exc})")

    @traced
    def _on_close_request(self, window: Gtk.ApplicationWindow) -> bool:
        """Renvoyer True inhibe la fermeture immédiate le temps de
        déconnecter proprement (RdpSession.disconnect: annule la tâche de
        réception de frames, ferme le pont presse-papier, referme la
        connexion FreeRDP) — la fenêtre se détruit elle-même une fois ce
        nettoyage terminé plutôt que de laisser la connexion en l'air.
        Protégé contre un double clic sur le bouton de fermeture pendant
        que le nettoyage est déjà en cours."""
        if self._shutting_down:
            return True
        self._shutting_down = True
        self._spawn(self._shutdown(window))
        return True

    @traced
    async def _shutdown(self, window: Gtk.ApplicationWindow) -> None:
        if self._session is not None:
            await self._session.disconnect()
            self._session = None
        window.destroy()
        self.quit()


@traced
def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logger.remove()
    logger.add(sys.stderr, level=args.log_level)

    try:
        options = build_rdp_options(args)
    except ValueError as exc:
        logger.error("Argument invalide : {}", exc)
        return 2

    # Importé ici seulement : l'intégrateur asyncio/GLib (gi.events, ou
    # gbulb sur un PyGObject ancien) n'est nécessaire qu'à l'exécution
    # réelle, pas pour parse_args()/build_rdp_options() ni pour DemoApp --
    # module importable sans display (tests/test_gtk4_demo_viewer.py).
    from gcm_gtk4_asyncio import run_application

    app = DemoApp(args, options)
    return run_application(app)


if __name__ == "__main__":
    sys.exit(main())
