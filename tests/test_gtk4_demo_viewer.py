# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_gtk4_demo_viewer.py — logique pure de l'assemblage GTK4 ajouté
le 2026-09-18 (gcm_gtk4_rdp_session.RdpSession, gcm_gtk4_demo_viewer.
parse_args/build_rdp_options), sans GTK4 installé ni display réel — même
principe et même doublure (tests/gtk_stub/) que test_gtk4_bridges.py pour
les deux ponts existants.

Ce que ceci teste : l'ORCHESTRATION (RdpSession assemble/désassemble
RdpView + ClipboardBridge dans le bon ordre, gère les échecs de connexion
et d'assemblage sans fuite, est idempotente) et le mapping CLI ->
RdpOptions (build_rdp_options/parse_args). Ce que ceci NE teste PAS : le
comportement réel de DemoApp (Gtk.Application) — une doublure de
Gtk.Application n'aurait aucune vraie sémantique de boucle GLib à
vérifier, ce serait tester un faux contre un autre faux. Voir plutôt
tests/test_gtk4_live.py::test_demo_session_assembles_real_window_display_and_clipboard_bridge,
qui exerce RdpSession dans une VRAIE Gtk.ApplicationWindow sous un VRAI
Gdk.Display (Xvfb) ; seule la couche protocole RDP y reste simulée (déjà
validée en conditions réelles ailleurs, cf. CLAUDE.md).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STUB_DIR = Path(__file__).resolve().parent / "gtk_stub"
_GTK4_DIR = _REPO_ROOT / "integrations" / "gtk4"


def _load_module_from_path(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _import_demo_modules_with_stub():
    """Importe gcm_gtk4_rdp_session et gcm_gtk4_demo_viewer (+ les deux
    ponts dont ils dépendent) avec gi/asyncrdp remplacés par les
    doublures de gtk_stub/, puis restaure sys.modules pour ne pas
    perturber les autres fichiers de tests — même technique que
    test_gtk4_bridges.py::_import_bridges_with_stub(), étendue aux deux
    nouveaux modules (dans leur ordre de dépendance)."""
    keys = (
        "gi",
        "gi.repository",
        "asyncrdp",
        "asyncrdp.tracing",
        "gcm_gtk4_display_bridge",
        "gcm_gtk4_clipboard_bridge",
        "gcm_gtk4_rdp_session",
        "gcm_gtk4_demo_viewer",
    )
    saved = {key: sys.modules.get(key) for key in keys}
    try:
        fake_gi = ModuleType("gi")
        sys.modules["gi"] = fake_gi
        stub_repository = _load_module_from_path("gi.repository", _STUB_DIR / "gi" / "repository.py")
        fake_gi.repository = stub_repository
        stub_asyncrdp = _load_module_from_path("asyncrdp", _STUB_DIR / "asyncrdp.py")
        # asyncrdp.tracing : voir le commentaire équivalent dans
        # test_gtk4_bridges.py::_import_bridges_with_stub() — même fichier
        # source réel chargé ici, pour ne pas faire diverger le
        # comportement testé de celui réellement appliqué.
        _load_module_from_path("asyncrdp.tracing", _REPO_ROOT / "src" / "asyncrdp" / "tracing.py")

        _load_module_from_path("gcm_gtk4_display_bridge", _GTK4_DIR / "gcm_gtk4_display_bridge.py")
        _load_module_from_path("gcm_gtk4_clipboard_bridge", _GTK4_DIR / "gcm_gtk4_clipboard_bridge.py")
        rdp_session = _load_module_from_path("gcm_gtk4_rdp_session", _GTK4_DIR / "gcm_gtk4_rdp_session.py")
        demo_viewer = _load_module_from_path("gcm_gtk4_demo_viewer", _GTK4_DIR / "gcm_gtk4_demo_viewer.py")
    finally:
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod
    return rdp_session, demo_viewer, stub_asyncrdp, stub_repository


rdp_session_module, demo_viewer_module, stub_asyncrdp, stub_gi = _import_demo_modules_with_stub()
RdpSession = rdp_session_module.RdpSession
parse_args = demo_viewer_module.parse_args
build_rdp_options = demo_viewer_module.build_rdp_options
Gdk = stub_gi.Gdk


# -------------------------------------------------------------------------
# Doublures
# -------------------------------------------------------------------------


def _make_fake_rdp_client():
    """Juste assez pour ClipboardBridge.__init__ (qui assigne des
    callables sur .clipboard.on_remote_*_changed / .get_local_*) — pas de
    .mouse/.keyboard/.get_frame() : RdpSession ne s'en sert jamais
    directement, seule RdpView (remplacée par _FakeView ci-dessous dans
    ces tests) en aurait besoin."""
    return types.SimpleNamespace(clipboard=types.SimpleNamespace())


class _FakeView:
    """Doublure de RdpView : RdpSession ne connaît celle-ci que par
    attach()/detach() (duck typing, voir gcm_gtk4_rdp_session.py) — pas
    besoin de la vraie classe (ni de son pompage de frames en tâche de
    fond) pour tester l'orchestration de RdpSession elle-même."""

    def __init__(self, *, raise_on_attach: Exception | None = None):
        self.attached_with: object | None = None
        self.detach_calls = 0
        self._raise_on_attach = raise_on_attach

    def attach(self, client) -> None:
        if self._raise_on_attach is not None:
            raise self._raise_on_attach
        self.attached_with = client

    def detach(self) -> None:
        self.detach_calls += 1
        self.attached_with = None


class _FakeConnectCtx:
    """Doublure du gestionnaire de contexte asynchrone renvoyé par
    asyncrdp.connect (même contrat __aenter__/__aexit__), sans réseau ni
    FreeRDP."""

    def __init__(self, client=None, *, raise_on_enter: Exception | None = None):
        self.client = client if client is not None else _make_fake_rdp_client()
        self._raise_on_enter = raise_on_enter
        self.enter_calls = 0
        self.exit_calls = 0

    async def __aenter__(self):
        self.enter_calls += 1
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self.client

    async def __aexit__(self, *exc_info) -> bool:
        self.exit_calls += 1
        return False


class _FakeConnectFn:
    """Doublure de asyncrdp.connect lui-même (le callable) : capture les
    arguments de chaque appel pour vérification, renvoie un
    _FakeConnectCtx configuré à l'avance plutôt que d'en créer un neuf
    (permet d'inspecter enter_calls/exit_calls après coup)."""

    def __init__(self, ctx: _FakeConnectCtx):
        self.ctx = ctx
        self.calls: list[tuple] = []

    def __call__(self, host, port, username, password, *, domain=None, options=None):
        self.calls.append((host, port, username, password, domain, options))
        return self.ctx


def _make_session(view=None, ctx=None, gdk_display=None):
    view = view if view is not None else _FakeView()
    ctx = ctx if ctx is not None else _FakeConnectCtx()
    connect_fn = _FakeConnectFn(ctx)
    session = RdpSession(
        view=view,
        gdk_display=gdk_display if gdk_display is not None else Gdk.Display(),
        connect_fn=connect_fn,
    )
    return session, view, ctx, connect_fn


# -------------------------------------------------------------------------
# RdpSession — connect()
# -------------------------------------------------------------------------


async def test_connect_attaches_view_and_wires_clipboard_bridge():
    session, view, ctx, _ = _make_session()

    client = await session.connect("192.168.1.10", 3389, "alice", "secret")

    assert client is ctx.client
    assert view.attached_with is client
    assert session.client is client
    assert session.connected is True
    # ClipboardBridge.__init__ (réel, chargé sous stub) assigne ces
    # callables sur le client — preuve que le pont a vraiment été
    # construit, pas seulement qu'aucune exception n'a été levée.
    assert client.clipboard.on_remote_text_changed is not None
    assert client.clipboard.get_local_text is not None


async def test_connect_forwards_exact_arguments_to_connect_fn():
    session, _, _, connect_fn = _make_session()
    sentinel_options = object()

    await session.connect("host", 3390, "bob", "pw", domain="CORP", options=sentinel_options)

    assert connect_fn.calls == [("host", 3390, "bob", "pw", "CORP", sentinel_options)]


async def test_double_connect_raises_runtime_error():
    session, _, _, _ = _make_session()
    await session.connect("host", 3389, "u", "p")

    with pytest.raises(RuntimeError):
        await session.connect("host", 3389, "u", "p")


async def test_connect_failure_does_not_attach_view():
    boom = ConnectionRefusedError("serveur injoignable")
    session, view, ctx, _ = _make_session(ctx=_FakeConnectCtx(raise_on_enter=boom))

    with pytest.raises(ConnectionRefusedError):
        await session.connect("host", 3389, "u", "p")

    assert view.attached_with is None
    assert session.connected is False
    assert ctx.exit_calls == 0  # __aenter__ n'a jamais rendu de client : rien à refermer


async def test_assembly_failure_after_connect_closes_the_connection():
    boom = RuntimeError("échec de construction du pont d'affichage")
    session, view, ctx, _ = _make_session(view=_FakeView(raise_on_attach=boom))

    with pytest.raises(RuntimeError):
        await session.connect("host", 3389, "u", "p")

    # La connexion a bien été établie (__aenter__ a rendu un client) mais
    # l'assemblage a échoué juste après : RdpSession doit refermer la
    # connexion plutôt que la laisser orpheline.
    assert ctx.enter_calls == 1
    assert ctx.exit_calls == 1
    assert session.connected is False
    assert view.attached_with is None


# -------------------------------------------------------------------------
# RdpSession — disconnect()
# -------------------------------------------------------------------------


async def test_disconnect_detaches_view_closes_clipboard_bridge_and_exits_context():
    session, view, ctx, _ = _make_session()
    client = await session.connect("host", 3389, "u", "p")

    await session.disconnect()

    assert view.detach_calls == 1
    assert ctx.exit_calls == 1
    assert session.client is None
    assert session.connected is False
    # ClipboardBridge.close() désabonne les callbacks côté client.
    assert client.clipboard.on_remote_text_changed is None


async def test_disconnect_without_prior_connect_is_a_safe_no_op():
    session, view, ctx, _ = _make_session()

    await session.disconnect()

    assert view.detach_calls == 1  # RdpView.detach() est sûre même sans attach() préalable
    assert ctx.exit_calls == 0  # rien à refermer : connect() n'a jamais été appelée


async def test_disconnect_is_idempotent():
    session, view, ctx, _ = _make_session()
    await session.connect("host", 3389, "u", "p")

    await session.disconnect()
    await session.disconnect()

    assert view.detach_calls == 2  # detach() est appelée à chaque disconnect(), sans effet de bord
    assert ctx.exit_calls == 1  # __aexit__, lui, ne doit être appelé qu'une seule fois


# -------------------------------------------------------------------------
# parse_args()
# -------------------------------------------------------------------------


def test_parse_args_defaults():
    args = parse_args(["10.0.0.5", "alice", "secret"])

    assert (args.host, args.username, args.password) == ("10.0.0.5", "alice", "secret")
    assert args.port == 3389
    assert args.domain is None
    assert (args.width, args.height) == (1280, 800)
    assert args.no_clipboard is False
    assert args.drive == []
    assert args.ignore_certificate is False
    assert args.log_level == "INFO"


def test_parse_args_drive_is_repeatable():
    args = parse_args(
        [
            "host",
            "u",
            "p",
            "--drive",
            "home:/home/mathilde",
            "--drive",
            "data:/srv/data",
        ]
    )

    assert args.drive == ["home:/home/mathilde", "data:/srv/data"]


# -------------------------------------------------------------------------
# build_rdp_options()
# -------------------------------------------------------------------------


def test_build_rdp_options_maps_basic_fields():
    args = parse_args(["host", "u", "p", "--width", "1024", "--height", "768", "--ignore-certificate"])

    options = build_rdp_options(args)

    assert (options.width, options.height) == (1024, 768)
    assert options.redirect_clipboard is True  # activé par défaut, cf. --no-clipboard ci-dessous
    assert options.redirect_drives is False
    assert options.drives == []
    assert options.ignore_certificate is True


def test_build_rdp_options_no_clipboard_flag_disables_redirection():
    args = parse_args(["host", "u", "p", "--no-clipboard"])

    options = build_rdp_options(args)

    assert options.redirect_clipboard is False


def test_build_rdp_options_drive_flag_builds_drive_mapping():
    args = parse_args(["host", "u", "p", "--drive", "home:/home/mathilde"])

    options = build_rdp_options(args)

    assert options.redirect_drives is True
    assert len(options.drives) == 1
    assert (options.drives[0].name, options.drives[0].path) == ("home", "/home/mathilde")


@pytest.mark.parametrize("malformed", ["sansdeuxpoints", ":/chemin", "nom:", ""])
def test_build_rdp_options_rejects_malformed_drive_entry(malformed):
    args = parse_args(["host", "u", "p", "--drive", malformed])

    with pytest.raises(ValueError):
        build_rdp_options(args)
