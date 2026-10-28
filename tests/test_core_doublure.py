# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de `_core.py` côté Python, avec une doublure du shim C (issue #50, palier 2).

Les classes `Keyboard`, `Mouse`, `Clipboard`, `Client`, les callbacks
`extern "Python"` et `connect()` ne font qu'orchestrer des appels au shim
FreeRDP. Ici, `_core.shim` est remplacé par `FauxShim` : chaque appel est
enregistré et renvoie une valeur choisie par le test. Les tampons restent de
vrais objets cffi (`ffi.new`), donc le marshaling Python <-> C est réellement
exercé ; seul le réseau RDP est absent. Le comportement contre un vrai
serveur reste couvert par `test_integration_live.py` (job integration).
"""

from __future__ import annotations

import asyncio
import os
import struct

import pytest

from asyncrdp import _core
from asyncrdp._core import (
    CF_DIB,
    CF_FILE_GROUP_DESCRIPTOR_W,
    CF_UNICODETEXT,
    FILECONTENTS_RANGE,
    FILECONTENTS_SIZE,
    Client,
    Clipboard,
    FreeRDPError,
    Keyboard,
    Mouse,
    RdpOptions,
    RemoteFileInfo,
    ffi,
)

CTX = ffi.cast("void *", 0x1000)
CLIPRDR = ffi.cast("void *", 0x2000)


class FauxShim:
    """Doublure du shim : enregistre les appels, renvoie `retours[nom]` (1 par défaut)."""

    def __init__(self, **retours) -> None:
        self.retours = retours
        self.appels: list[tuple[str, tuple]] = []
        self.framebuffer: bytes | None = None
        self.fds: list[int] = []
        self._garde: list = []

    def __getattr__(self, nom: str):
        if nom.startswith("_"):
            raise AttributeError(nom)

        def appel(*args):
            self.appels.append((nom, args))
            retour = self.retours.get(nom, 1)
            return retour(*args) if callable(retour) and not isinstance(retour, ffi.CData) else retour

        return appel

    def asyncrdp_get_framebuffer(self, ctx, data_ptr, width, height, stride):
        self.appels.append(("asyncrdp_get_framebuffer", (ctx,)))
        if self.framebuffer is None:
            return 0
        tampon = ffi.new("unsigned char[]", self.framebuffer)
        self._garde.append(tampon)
        data_ptr[0], width[0], height[0], stride[0] = tampon, 2, 1, len(self.framebuffer)
        return 1

    def asyncrdp_get_handle_fds(self, ctx, buf, taille):
        self.appels.append(("asyncrdp_get_handle_fds", (ctx,)))
        for i, fd in enumerate(self.fds[:taille]):
            buf[i] = fd
        return len(self.fds)

    def noms(self) -> list[str]:
        return [nom for nom, _ in self.appels]

    def arguments(self, nom: str) -> list[tuple]:
        return [args for n, args in self.appels if n == nom]


@pytest.fixture
def faux(monkeypatch) -> FauxShim:
    doublure = FauxShim()
    monkeypatch.setattr(_core, "shim", doublure)
    return doublure


@pytest.fixture
async def client(faux):
    """Client enregistré dans la table des contextes, comme le fait connect()."""
    c = Client(CTX, asyncio.get_running_loop())
    _core._clients_by_context[_core._ctx_key(CTX)] = c
    yield c
    _core._clients_by_context.pop(_core._ctx_key(CTX), None)


async def _vider_boucle() -> None:
    """Laisse tourner les call_soon_threadsafe et les tâches de fond."""
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Clavier et souris
# ---------------------------------------------------------------------------


class TestClavier:
    def test_write_envoie_appui_puis_relache_par_caractere(self, faux) -> None:
        Keyboard(CTX).write("aé")
        assert faux.arguments("asyncrdp_send_unicode_key") == [
            (CTX, ord("a"), 1),
            (CTX, ord("a"), 0),
            (CTX, ord("é"), 1),
            (CTX, ord("é"), 0),
        ]

    def test_key_press(self, faux) -> None:
        faux.retours["asyncrdp_scancode_from_name"] = 0x1C
        Keyboard(CTX).key_press("return")
        assert faux.arguments("asyncrdp_scancode_from_name") == [(b"return",)]
        assert faux.arguments("asyncrdp_send_scancode_key") == [(CTX, 0x1C, 1), (CTX, 0x1C, 0)]

    def test_key_press_touche_inconnue(self, faux) -> None:
        faux.retours["asyncrdp_scancode_from_name"] = 0
        with pytest.raises(ValueError, match="touche inconnue"):
            Keyboard(CTX).key_press("zzz")
        assert "asyncrdp_send_scancode_key" not in faux.noms()


class TestSouris:
    def test_move_puis_click_a_la_position_courante(self, faux) -> None:
        faux.retours["asyncrdp_ptr_flag_button"] = 0x1000
        souris = Mouse(CTX)
        souris.move(10, 20)
        souris.click("left")
        souris.scroll(-120)
        assert faux.arguments("asyncrdp_send_mouse_move") == [(CTX, 10, 20)]
        assert faux.arguments("asyncrdp_send_mouse_button") == [(CTX, 10, 20, 0x1000, 1), (CTX, 10, 20, 0x1000, 0)]
        assert faux.arguments("asyncrdp_send_mouse_wheel") == [(CTX, 10, 20, -120)]

    @pytest.mark.parametrize("methode", ["button_press", "button_release"])
    def test_bouton_inconnu(self, faux, methode) -> None:
        faux.retours["asyncrdp_ptr_flag_button"] = 0
        with pytest.raises(ValueError, match="bouton inconnu"):
            getattr(Mouse(CTX), methode)("milieu-gauche")
        assert "asyncrdp_send_mouse_button" not in faux.noms()


# ---------------------------------------------------------------------------
# Callbacks extern "Python" (appelés par le shim C)
# ---------------------------------------------------------------------------

_INCONNU = ffi.cast("void *", 0xDEAD)


class TestCallbacks:
    @pytest.mark.parametrize(
        ("callback", "args"),
        [
            (_core.asyncrdp_on_end_paint, ()),
            (_core.asyncrdp_on_disp_ready, (ffi.NULL,)),
            (_core.asyncrdp_on_gfx_ready, (ffi.NULL,)),
            (_core.asyncrdp_on_cliprdr_ready, (ffi.NULL,)),
            (_core.asyncrdp_on_clipboard_format_list, (ffi.NULL, ffi.NULL, ffi.NULL, 0)),
            (_core.asyncrdp_on_clipboard_data_request, (ffi.NULL, 13)),
            (_core.asyncrdp_on_clipboard_data_response, (ffi.NULL, ffi.NULL, 0)),
            (_core.asyncrdp_on_clipboard_file_contents_request, (ffi.NULL, 1, 0, 0, 0, 0, 0)),
            (_core.asyncrdp_on_clipboard_file_contents_response, (ffi.NULL, 1, ffi.NULL, 0)),
        ],
    )
    def test_contexte_inconnu_ignore(self, faux, callback, args) -> None:
        assert callback(_INCONNU, *args) == 1
        assert faux.appels == []

    def test_disp_et_cliprdr_prets(self, client) -> None:
        disp = ffi.cast("void *", 0x3000)
        assert _core.asyncrdp_on_disp_ready(CTX, disp) == 1
        assert _core.asyncrdp_on_cliprdr_ready(CTX, CLIPRDR) == 1
        assert client._disp == disp
        assert client.clipboard._cliprdr == CLIPRDR

    def test_gfx_pret_avant_gdi_ne_cable_rien(self, client, faux) -> None:
        gfx = ffi.cast("void *", 0x4000)
        assert _core.asyncrdp_on_gfx_ready(CTX, gfx) == 1
        assert client._gfx == gfx
        assert "asyncrdp_init_graphics_pipeline" not in faux.noms()

    @pytest.mark.parametrize("reussite", [1, 0])
    def test_gfx_pret_apres_gdi_cable_tardivement(self, client, faux, reussite) -> None:
        faux.retours["asyncrdp_init_graphics_pipeline"] = reussite
        client._gdi_ready = True
        client._options_wants_gfx = True
        gfx = ffi.cast("void *", 0x4000)
        assert _core.asyncrdp_on_gfx_ready(CTX, gfx) == 1
        assert faux.arguments("asyncrdp_init_graphics_pipeline") == [(CTX, gfx)]

    async def test_format_list_copie_les_noms_avant_de_rendre_la_main(self, client, monkeypatch) -> None:
        recus = []
        monkeypatch.setattr(client.clipboard, "_on_server_format_list", lambda c, f: recus.append((c, f)))
        nom = ffi.new("char[]", b"HTML Format")
        ids = ffi.new("unsigned int[]", [13, 49300])
        noms = ffi.new("char*[]", [ffi.NULL, nom])
        assert _core.asyncrdp_on_clipboard_format_list(CTX, CLIPRDR, ids, noms, 2) == 1
        await _vider_boucle()
        assert recus == [(CLIPRDR, [(13, None), (49300, "HTML Format")])]

    async def test_relais_data_request_response_et_file_contents(self, client, monkeypatch) -> None:
        recus = []
        cb = client.clipboard
        monkeypatch.setattr(cb, "_on_server_data_request", lambda *a: recus.append(("request", a)))
        monkeypatch.setattr(cb, "_on_server_data_response", lambda *a: recus.append(("response", a)))
        monkeypatch.setattr(cb, "_on_server_file_contents_request", lambda *a: recus.append(("fc_request", a)))
        monkeypatch.setattr(cb, "_on_server_file_contents_response", lambda *a: recus.append(("fc_response", a)))
        donnees = ffi.new("unsigned char[]", b"abc")
        _core.asyncrdp_on_clipboard_data_request(CTX, CLIPRDR, 13)
        _core.asyncrdp_on_clipboard_data_response(CTX, CLIPRDR, donnees, 3)
        _core.asyncrdp_on_clipboard_file_contents_request(CTX, CLIPRDR, 7, 0, FILECONTENTS_RANGE, 1, 2, 3)
        _core.asyncrdp_on_clipboard_file_contents_response(CTX, CLIPRDR, 7, donnees, 3)
        _core.asyncrdp_on_clipboard_file_contents_response(CTX, CLIPRDR, 8, ffi.NULL, 0)
        await _vider_boucle()
        assert recus == [
            ("request", (CLIPRDR, 13)),
            ("response", (b"abc",)),
            ("fc_request", (CLIPRDR, 7, 0, FILECONTENTS_RANGE, 1, 2, 3)),
            ("fc_response", (7, b"abc")),
            ("fc_response", (8, b"")),
        ]

    async def test_end_paint_pousse_la_frame(self, client, faux) -> None:
        faux.framebuffer = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        assert _core.asyncrdp_on_end_paint(CTX) == 1
        await _vider_boucle()
        assert client.frame_size == (2, 1)
        assert await client.get_frame() == faux.framebuffer

    def test_end_paint_sans_framebuffer(self, client, faux) -> None:
        assert _core.asyncrdp_on_end_paint(CTX) == 1
        assert client.frames.empty()


# ---------------------------------------------------------------------------
# Presse-papier
# ---------------------------------------------------------------------------


@pytest.fixture
async def presse_papier(faux):
    cb = Clipboard(CTX, asyncio.get_running_loop())
    cb._cliprdr = CLIPRDR
    return cb


def _reponse_differee(cb: Clipboard, raw: bytes) -> None:
    """Simule le serveur : répond dès que la demande est partie."""
    cb._loop.call_soon(cb._on_server_data_response, raw)


class TestPressePapierReception:
    async def test_format_list_texte(self, presse_papier, faux) -> None:
        recus = []
        presse_papier.on_remote_text_changed = recus.append
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(
            presse_papier, "salut\x00".encode("utf-16-le")
        )
        presse_papier._on_server_format_list(CLIPRDR, [(CF_UNICODETEXT, None)])
        await _vider_boucle()
        assert recus == ["salut"]

    async def test_format_list_texte_sans_callback(self, presse_papier, faux) -> None:
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(presse_papier, b"a\x00")
        presse_papier._on_server_format_list(CLIPRDR, [(1, None)])
        await _vider_boucle()
        assert faux.arguments("asyncrdp_clipboard_request_data") == [(CLIPRDR, CF_UNICODETEXT)]

    @pytest.mark.parametrize("format_image", [CF_DIB, 17])
    async def test_format_list_image(self, presse_papier, faux, format_image) -> None:
        recus = []
        presse_papier.on_remote_image_changed = recus.append
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(presse_papier, b"DIB")
        presse_papier._on_server_format_list(CLIPRDR, [(format_image, None)])
        await _vider_boucle()
        assert recus == [b"DIB"]

    async def test_format_list_image_vide_non_signalee(self, presse_papier, faux) -> None:
        recus = []
        presse_papier.on_remote_image_changed = recus.append
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(presse_papier, b"")
        presse_papier._on_server_format_list(CLIPRDR, [(CF_DIB, None)])
        await _vider_boucle()
        assert recus == []

    async def test_format_list_fichiers(self, presse_papier, faux, tmp_path) -> None:
        fichier = tmp_path / "a.txt"
        fichier.write_bytes(b"12345")
        descripteur, _ = _core._pack_file_group_descriptor([str(fichier)])
        recus = []
        presse_papier.on_remote_files_changed = recus.append
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(presse_papier, descripteur)
        presse_papier._on_server_format_list(CLIPRDR, [(49001, CF_FILE_GROUP_DESCRIPTOR_W)])
        await _vider_boucle()
        assert faux.arguments("asyncrdp_clipboard_request_data") == [(CLIPRDR, 49001)]
        assert [(f.name, f.size) for f in recus[0]] == [("a.txt", 5)]

    async def test_format_list_fichiers_sans_callback(self, presse_papier, faux) -> None:
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(
            presse_papier, struct.pack("<I", 0)
        )
        presse_papier._on_server_format_list(CLIPRDR, [(49001, CF_FILE_GROUP_DESCRIPTOR_W)])
        await _vider_boucle()
        assert presse_papier._remote_files == []

    def test_format_list_non_supporte(self, presse_papier, faux) -> None:
        presse_papier._on_server_format_list(CLIPRDR, [(49300, "HTML Format")])
        assert presse_papier._background_tasks == set()
        assert presse_papier._format_ids == {"HTML Format": 49300}

    def test_reponse_sans_demande_en_cours_ignoree(self, presse_papier) -> None:
        presse_papier._on_server_data_response(b"orphelin")
        assert presse_papier._pending_data_request is None


class TestPressePapierDemandes:
    async def test_sans_canal_cliprdr(self, faux) -> None:
        cb = Clipboard(CTX, asyncio.get_running_loop())
        assert await cb.request_remote_text() == ""
        assert await cb.request_remote_file_contents(0) == b""
        cb.announce_local_text("x")
        assert faux.appels == []

    async def test_texte_vide(self, presse_papier, faux) -> None:
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(presse_papier, b"")
        assert await presse_papier.request_remote_text() == ""

    async def test_texte_non_decodable(self, presse_papier, faux) -> None:
        faux.retours["asyncrdp_clipboard_request_data"] = lambda *a: _reponse_differee(presse_papier, b"\x00\xd8")
        assert await presse_papier.request_remote_text() == ""

    async def test_timeout_serveur(self, presse_papier, monkeypatch) -> None:
        async def expire(attente, timeout):
            raise asyncio.TimeoutError

        monkeypatch.setattr(_core.asyncio, "wait_for", expire)
        assert await presse_papier.request_remote_image() == b""

    async def test_liste_fichiers_format_inconnu(self, presse_papier, faux) -> None:
        assert await presse_papier.request_remote_file_list() == []
        assert faux.appels == []

    async def test_contenu_fichier_en_plusieurs_blocs(self, presse_papier, faux, monkeypatch) -> None:
        monkeypatch.setattr(_core, "_FILE_CHUNK_SIZE", 4)
        presse_papier._remote_files = [RemoteFileInfo(0, "a.bin", 10, False)]
        contenu = b"0123456789"

        def serveur(cliprdr, stream_id, index, flags, bas, haut, taille):
            debut = (haut << 32) | bas
            presse_papier._loop.call_soon(
                presse_papier._on_server_file_contents_response, stream_id, contenu[debut : debut + taille]
            )

        faux.retours["asyncrdp_clipboard_request_file_contents"] = serveur
        assert await presse_papier.request_remote_file_contents(0) == contenu
        assert [a[1:] for a in faux.arguments("asyncrdp_clipboard_request_file_contents")] == [
            (1, 0, FILECONTENTS_RANGE, 0, 0, 4),
            (2, 0, FILECONTENTS_RANGE, 4, 0, 4),
            (3, 0, FILECONTENTS_RANGE, 8, 0, 2),
        ]

    async def test_contenu_fichier_index_inconnu(self, presse_papier, faux) -> None:
        assert await presse_papier.request_remote_file_contents(3) == b""
        assert faux.appels == []

    async def test_contenu_fichier_bloc_vide(self, presse_papier, faux) -> None:
        presse_papier._remote_files = [RemoteFileInfo(0, "a.bin", 10, False)]
        faux.retours["asyncrdp_clipboard_request_file_contents"] = lambda c, sid, *a: presse_papier._loop.call_soon(
            presse_papier._on_server_file_contents_response, sid, b""
        )
        assert await presse_papier.request_remote_file_contents(0) == b""

    async def test_contenu_fichier_timeout(self, presse_papier, monkeypatch) -> None:
        async def expire(attente, timeout):
            raise asyncio.TimeoutError

        monkeypatch.setattr(_core.asyncio, "wait_for", expire)
        presse_papier._remote_files = [RemoteFileInfo(0, "a.bin", 10, False)]
        assert await presse_papier.request_remote_file_contents(0) == b""
        assert presse_papier._pending_file_requests == {}

    def test_reponse_fichier_stream_inconnu(self, presse_papier) -> None:
        presse_papier._on_server_file_contents_response(99, b"x")
        assert presse_papier._pending_file_requests == {}


def _reponses(faux: FauxShim) -> list[bytes]:
    """Contenus envoyés via send_data_response (b"" pour un NULL)."""
    sorties = []
    for _, buf, taille in faux.arguments("asyncrdp_clipboard_send_data_response"):
        sorties.append(b"" if buf == ffi.NULL else bytes(ffi.buffer(buf, taille)))
    return sorties


class TestPressePapierEnvoi:
    def test_annonces(self, presse_papier, faux, tmp_path) -> None:
        presse_papier.announce_local_text("bonjour")
        presse_papier.announce_local_image()
        presse_papier.announce_local_files([str(tmp_path)])
        envoyes = []
        for _, ids, noms, n in faux.arguments("asyncrdp_clipboard_send_format_list"):
            envoyes.append([(ids[i], ffi.string(noms[i]).decode() if noms[i] != ffi.NULL else None) for i in range(n)])
        assert envoyes == [[(CF_UNICODETEXT, None)], [(CF_DIB, None)], [(0, CF_FILE_GROUP_DESCRIPTOR_W)]]
        assert presse_papier._pending_local_selection == [str(tmp_path)]

    def test_demande_texte(self, presse_papier, faux) -> None:
        presse_papier.get_local_text = lambda: "é"
        presse_papier._on_server_data_request(CLIPRDR, CF_UNICODETEXT)
        presse_papier.get_local_text = None
        presse_papier._on_server_data_request(CLIPRDR, 1)
        assert _reponses(faux) == ["é\x00".encode("utf-16-le"), "\x00".encode("utf-16-le")]

    def test_demande_image(self, presse_papier, faux) -> None:
        presse_papier._on_server_data_request(CLIPRDR, CF_DIB)
        presse_papier.get_local_image_dib = lambda: b"DIB"
        presse_papier._on_server_data_request(CLIPRDR, 17)
        assert _reponses(faux) == [b"", b"DIB"]

    def test_demande_format_non_supporte(self, presse_papier, faux) -> None:
        presse_papier._on_server_data_request(CLIPRDR, 49300)
        assert _reponses(faux) == [b""]

    @pytest.mark.parametrize("par_callback", [False, True])
    def test_demande_fichiers(self, presse_papier, faux, tmp_path, par_callback) -> None:
        fichier = tmp_path / "f.txt"
        fichier.write_bytes(b"x")
        presse_papier._format_ids[CF_FILE_GROUP_DESCRIPTOR_W] = 49001
        if par_callback:
            presse_papier.get_local_files = lambda: [str(fichier)]
        else:
            presse_papier._pending_local_selection = [str(fichier)]
        presse_papier._on_server_data_request(CLIPRDR, 49001)
        assert presse_papier._local_files == [str(fichier)]
        assert _reponses(faux) == [_core._pack_file_group_descriptor([str(fichier)])[0]]


def _contenus(faux: FauxShim) -> list[tuple[int, bytes]]:
    sorties = []
    for _, stream_id, buf, taille in faux.arguments("asyncrdp_clipboard_send_file_contents_response"):
        sorties.append((stream_id, b"" if buf == ffi.NULL else bytes(ffi.buffer(buf, taille))))
    return sorties


class TestPressePapierContenuLocal:
    def test_taille_puis_plage(self, presse_papier, faux, tmp_path) -> None:
        fichier = tmp_path / "f.bin"
        fichier.write_bytes(b"abcdef")
        presse_papier._local_files = [str(fichier)]
        presse_papier._on_server_file_contents_request(CLIPRDR, 1, 0, FILECONTENTS_SIZE, 0, 0, 8)
        presse_papier._on_server_file_contents_request(CLIPRDR, 2, 0, FILECONTENTS_RANGE, 2, 0, 3)
        assert _contenus(faux) == [(1, struct.pack("<Q", 6)), (2, b"cde")]

    def test_index_inconnu_dossier_et_fichier_disparu(self, presse_papier, faux, tmp_path) -> None:
        presse_papier._local_files = [None, str(tmp_path / "absent")]
        for stream_id, index in ((1, 5), (2, 0), (3, 1)):
            presse_papier._on_server_file_contents_request(CLIPRDR, stream_id, index, FILECONTENTS_SIZE, 0, 0, 8)
        assert _contenus(faux) == [(1, b""), (2, b""), (3, b"")]


# ---------------------------------------------------------------------------
# Client : resize, frames, fds, déconnexion
# ---------------------------------------------------------------------------


class TestClient:
    def test_resize(self, client, faux) -> None:
        assert client.request_resize(800, 600) is False
        client._disp = ffi.cast("void *", 0x3000)
        assert client.request_resize(800, 600) is True
        faux.retours["asyncrdp_send_resize"] = 0
        assert client.request_resize(800, 600) is False

    def test_push_frame_jette_l_ancienne(self, client) -> None:
        client._push_frame(b"ancienne", (1, 1))
        client._push_frame(b"recente", (2, 2))
        assert client.frames.get_nowait() == b"recente"
        assert client.frame_size == (2, 2)

    async def test_sync_fds_ajoute_puis_retire(self, faux) -> None:
        c = Client(CTX, asyncio.get_running_loop())
        lecture, ecriture = os.pipe()
        try:
            faux.fds = [lecture]
            c._sync_fds()
            assert c._registered_fds == {lecture}
            c._sync_fds()  # aucun changement
            faux.fds = []
            c._sync_fds()
            assert c._registered_fds == set()
            faux.fds = [lecture]
            c._sync_fds()
            c._unregister_fds()
            assert c._registered_fds == set()
        finally:
            os.close(lecture)
            os.close(ecriture)

    async def test_fd_lisible_ok_resynchronise(self, faux) -> None:
        c = Client(CTX, asyncio.get_running_loop())
        faux.retours["asyncrdp_shall_disconnect"] = 0
        c._on_fd_readable(5)
        assert faux.noms()[-1] == "asyncrdp_get_handle_fds"
        assert not c._disconnect_event.is_set()

    async def test_fd_lisible_deconnexion_demandee(self, faux) -> None:
        c = Client(CTX, asyncio.get_running_loop())
        c._on_fd_readable(5)
        assert c._disconnect_event.is_set()
        assert c.last_error is None

    @pytest.mark.parametrize(("message", "attendu"), [(b"connexion perdue", "connexion perdue"), (None, "inconnue")])
    async def test_fd_lisible_erreur(self, faux, message, attendu) -> None:
        texte = ffi.new("char[]", message) if message else ffi.NULL
        faux.retours.update(
            asyncrdp_check_event_handles=0,
            asyncrdp_get_last_error=0x2000C,
            asyncrdp_get_last_error_string=texte,
        )
        c = Client(CTX, asyncio.get_running_loop())
        c._on_fd_readable(5)
        assert isinstance(c.last_error, FreeRDPError)
        assert attendu in str(c.last_error)
        assert c._disconnect_event.is_set()
        await c.wait_disconnected()

    async def test_refresh_periodique_puis_deconnexion(self, faux) -> None:
        c = Client(CTX, asyncio.get_running_loop())
        c._register_fds()
        assert c._refresh_handle is not None
        c._disconnect_event.set()
        c._schedule_refresh()
        c._gfx = ffi.cast("void *", 0x4000)
        await c.disconnect()
        assert c._refresh_handle.cancelled()
        assert "asyncrdp_uninit_graphics_pipeline" in faux.noms()
        assert faux.noms()[-1] == "asyncrdp_disconnect"


# ---------------------------------------------------------------------------
# connect() de bout en bout, sans réseau
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_chemin_nominal_puis_nettoyage(self, faux) -> None:
        faux.retours.update(asyncrdp_context_new=CTX, asyncrdp_shall_disconnect=0)
        async with _core.connect("h", username="u", password="p", domain="D") as c:
            assert _core._clients_by_context[_core._ctx_key(CTX)] is c
            assert c._gdi_ready
        noms = faux.noms()
        assert noms.index("asyncrdp_register_channels") < noms.index("asyncrdp_connect")
        assert noms[-1] == "asyncrdp_context_free"
        assert _core._ctx_key(CTX) not in _core._clients_by_context
        assert faux.arguments("asyncrdp_set_domain") == [(CTX, b"D")]

    async def test_addins_en_echec_et_gfx_deja_pret(self, faux) -> None:
        def connexion(ctx):
            # Le canal RDPGFX arrive pendant la négociation (thread executor).
            _core.asyncrdp_on_gfx_ready(ctx, ffi.cast("void *", 0x4000))
            return 1

        faux.retours.update(asyncrdp_context_new=CTX, asyncrdp_load_addins=0, asyncrdp_connect=connexion)
        async with _core.connect("h", options=RdpOptions(enable_graphics_pipeline=True)) as c:
            assert c._options_wants_gfx
        assert faux.arguments("asyncrdp_init_graphics_pipeline") == [(CTX, ffi.cast("void *", 0x4000))]
        assert "asyncrdp_set_username" not in faux.noms()

    async def test_gfx_cablage_en_echec_et_canal_absent(self, faux) -> None:
        faux.retours.update(asyncrdp_context_new=CTX, asyncrdp_init_graphics_pipeline=0)
        faux.retours["asyncrdp_connect"] = lambda ctx: _core.asyncrdp_on_gfx_ready(ctx, ffi.cast("void *", 0x4000))
        async with _core.connect("h", options=RdpOptions(enable_graphics_pipeline=True)):
            pass
        faux.retours["asyncrdp_connect"] = 1
        async with _core.connect("h", options=RdpOptions(enable_graphics_pipeline=True)) as c:
            assert c._gfx is None

    async def test_contexte_impossible(self, faux) -> None:
        faux.retours["asyncrdp_context_new"] = ffi.NULL
        with pytest.raises(FreeRDPError, match="création du contexte"):
            async with _core.connect("h"):
                pass

    async def test_reglages_refuses(self, faux) -> None:
        faux.retours.update(asyncrdp_context_new=CTX, asyncrdp_set_port=0)
        with pytest.raises(FreeRDPError, match="rdpSettings"):
            async with _core.connect("h"):
                pass
        assert faux.noms()[-1] == "asyncrdp_context_free"

    async def test_connexion_refusee(self, faux) -> None:
        faux.retours.update(
            asyncrdp_context_new=CTX,
            asyncrdp_connect=0,
            asyncrdp_get_last_error=0x20009,
            asyncrdp_get_last_error_string=ffi.NULL,
        )
        with pytest.raises(FreeRDPError, match="erreur inconnue"):
            async with _core.connect("h"):
                pass
        assert _core._ctx_key(CTX) not in _core._clients_by_context
        assert faux.noms()[-1] == "asyncrdp_context_free"

    async def test_gdi_en_echec(self, faux) -> None:
        faux.retours.update(asyncrdp_context_new=CTX, asyncrdp_gdi_init=0)
        with pytest.raises(FreeRDPError, match="GDI"):
            async with _core.connect("h"):
                pass
        assert _core._ctx_key(CTX) not in _core._clients_by_context
