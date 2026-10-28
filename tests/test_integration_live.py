# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests d'intégration — nécessitent un vrai serveur RDP accessible.

Désactivés par défaut (skip propre) tant que ASYNCRDP_TEST_HOST n'est pas
renseigné :

    export ASYNCRDP_TEST_HOST=127.0.0.1
    export ASYNCRDP_TEST_USER=rdptest
    export ASYNCRDP_TEST_PASSWORD=testpass123
    pytest tests/test_integration_live.py -v

Serveur d'essai sans Windows (job CI « Intégration », issue #41) :
freerdp-shadow-cli sur un Xvfb, base SAM produite par winpr-hash, et
ASYNCRDP_TEST_NO_DISP=1 (ce serveur n'ouvre pas le canal Display Control).

Ce fichier reprend, sous forme de vraies assertions pytest, les scénarios
qui ont été validés manuellement pendant le développement (voir CLAUDE.md
pour le compte-rendu complet et les bugs trouvés à cette occasion) :
connexion de base, input, resize, clipboard texte. Les scénarios qui
nécessitent en plus un accès shell à la machine hébergeant la session
(clipboard "pull" via xclip, disque redirigé) ne sont PAS repris ici —
voir examples/test_full_suite.py pour ceux-là, qui prennent des options
dédiées pour ce cas.
"""

import asyncio
import os

import pytest

import asyncrdp

pytestmark = pytest.mark.integration


@pytest.fixture
async def connected_client(live_server_config):
    host, port, username, password = live_server_config
    options = asyncrdp.RdpOptions(
        width=1280,
        height=800,
        redirect_clipboard=True,
        ignore_certificate=True,
    )
    async with asyncrdp.connect(host, port, username, password, options=options) as client:
        yield client


@pytest.mark.asyncio
async def test_connect_and_get_frame(connected_client):
    raw = await asyncio.wait_for(connected_client.get_frame(), timeout=10.0)
    assert len(raw) > 0
    width, height = connected_client.frame_size
    assert width == 1280
    assert height == 800
    assert len(raw) == width * height * 4  # BGRA32, pas de padding attendu ici


@pytest.mark.asyncio
async def test_input_does_not_raise(connected_client):
    connected_client.mouse.move(100, 100)
    connected_client.mouse.click("left")
    connected_client.keyboard.write("test")
    connected_client.keyboard.key_press("return")
    # Pas d'assertion de contenu (pas de vérification visuelle possible
    # ici) — le test vérifie seulement l'absence d'exception.


@pytest.mark.asyncio
async def test_resize(connected_client):
    if os.environ.get("ASYNCRDP_TEST_NO_DISP") == "1":
        # freerdp-shadow-cli (serveur d'essai de la CI, issue #41) n'ouvre pas
        # le canal Display Control : redimensionnement impossible côté serveur.
        pytest.skip("serveur sans canal Display Control (ASYNCRDP_TEST_NO_DISP=1)")
    # Le canal disp (Display Control) se connecte de façon asynchrone,
    # potentiellement juste après que connect() ait rendu la main — on
    # attend qu'il soit prêt plutôt que d'appeler request_resize() à
    # l'aveugle (sinon il échoue silencieusement en renvoyant False,
    # cf. request_resize() qui logue "canal disp pas encore prêt").
    for _ in range(20):
        if connected_client._disp is not None:
            break
        await asyncio.sleep(0.25)
    else:
        pytest.fail("Canal Display Control jamais prêt après 5s")

    ok = connected_client.request_resize(1024, 640)
    assert ok
    # Laisse le temps au serveur de répondre par de nouvelles frames.
    await asyncio.sleep(2.0)
    raw = await asyncio.wait_for(connected_client.get_frame(), timeout=5.0)
    assert len(raw) > 0


@pytest.mark.asyncio
async def test_clipboard_text_roundtrip(connected_client):
    """Annonce un texte local, vérifie que la demande de contenu à nous-
    mêmes (le serveur va nous le redemander) ne lève pas d'exception.
    Un vrai test de contenu round-trip nécessite un second point de
    contrôle côté serveur — voir test_full_suite.py --session-display."""
    sent_text = "asyncrdp pytest integration test"
    connected_client.clipboard.get_local_text = lambda: sent_text
    connected_client.clipboard.announce_local_text(sent_text)
    await asyncio.sleep(1.0)
    # Pas d'assertion forte sur le retour ici : dépend du comportement du
    # serveur de test (certains ne redemandent le contenu que si un
    # gestionnaire de presse-papier actif le consomme réellement).


@pytest.mark.asyncio
async def test_disconnect_is_clean(live_server_config):
    """Une connexion suivie d'une déconnexion immédiate ne doit jamais
    lever d'exception ni laisser de tâche en suspens."""
    host, port, username, password = live_server_config
    options = asyncrdp.RdpOptions(ignore_certificate=True)
    async with asyncrdp.connect(host, port, username, password, options=options) as client:
        await asyncio.wait_for(client.get_frame(), timeout=10.0)
    # La sortie du `async with` a déjà appelé disconnect() — si on arrive
    # ici sans exception, c'est le comportement attendu.
