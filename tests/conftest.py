# SPDX-License-Identifier: GPL-3.0-or-later
"""
conftest.py — fixtures partagées.

Les tests de ce dossier se répartissent en deux catégories :

- Tests unitaires (test_file_group_descriptor.py, test_rdp_options.py) :
  aucune dépendance réseau, tournent partout où le package est installé
  (donc où FreeRDP est disponible pour compiler le shim — l'import de
  `asyncrdp` charge le module compilé même pour tester de la pure logique
  Python, puisque _core.py importe le shim au niveau module).

- Tests d'intégration (test_integration_live.py) : nécessitent un vrai
  serveur RDP accessible. Désactivés par défaut, activés en renseignant
  les variables d'environnement ASYNCRDP_TEST_HOST (+ _USER/_PASSWORD/
  _PORT optionnels) — voir ce fichier pour le détail. Sans ces variables,
  ils sont marqués `skip` proprement plutôt que d'échouer.

- Tests GTK4 « live » (test_gtk4_live.py) : nécessitent un vrai GTK4 +
  un vrai serveur d'affichage X11 (Xvfb suffit, pas besoin d'un vrai
  écran). Désactivés par défaut, activés avec ASYNCRDP_TEST_GTK4=1 —
  voir ce fichier pour le détail de la mise en place (Xvfb, xclip, xwd,
  imagemagick).
"""

import os
from pathlib import Path

import pytest

_NATIVE_TYPELIB = (
    Path(__file__).resolve().parent.parent
    / "integrations"
    / "gtk4"
    / "native"
    / "_build"
    / "AsyncrdpClipboard-1.0.typelib"
)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: nécessite un vrai serveur RDP (voir ASYNCRDP_TEST_HOST)")
    config.addinivalue_line(
        "markers", "gtk4_live: nécessite un vrai GTK4 + un vrai serveur X (voir ASYNCRDP_TEST_GTK4)"
    )


@pytest.fixture
def live_server_config():
    """Renvoie (host, port, username, password) ou déclenche un skip si
    ASYNCRDP_TEST_HOST n'est pas renseigné."""
    host = os.environ.get("ASYNCRDP_TEST_HOST")
    if not host:
        pytest.skip("ASYNCRDP_TEST_HOST non renseigné — test d'intégration ignoré")
    port = int(os.environ.get("ASYNCRDP_TEST_PORT", "3389"))
    username = os.environ.get("ASYNCRDP_TEST_USER", "")
    password = os.environ.get("ASYNCRDP_TEST_PASSWORD", "")
    return host, port, username, password


@pytest.fixture
def gtk4_live_enabled():
    """Déclenche un skip proprement si ASYNCRDP_TEST_GTK4 n'est pas
    renseigné à 1 — voir tests/test_gtk4_live.py pour la mise en place
    requise (Xvfb, xclip, xwd/imagemagick)."""
    if os.environ.get("ASYNCRDP_TEST_GTK4") != "1":
        pytest.skip("ASYNCRDP_TEST_GTK4=1 non renseigné — test GTK4 live ignoré")


@pytest.fixture
def native_clipboard_provider_available():
    """Déclenche un skip proprement si integrations/gtk4/native/ (voir
    build_gir.py à la racine) n'a pas été compilé — prérequis de build
    optionnel, distinct de gtk4_live_enabled ci-dessus (qui ne concerne
    que l'environnement d'affichage). Un typelib absent n'est pas un bug :
    c'est le cas normal d'un checkout où build_gir.py n'a jamais été
    lancé (voir CLAUDE.md, section presse-papier image)."""
    if not _NATIVE_TYPELIB.is_file():
        pytest.skip(
            f"{_NATIVE_TYPELIB} absent — lancer `python3 build_gir.py` d'abord "
            "(voir CLAUDE.md, section presse-papier image)"
        )
