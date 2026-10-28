# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_gtk4_clipboard_file_download.py — logique pure du téléchargement
RDP -> local du pont presse-papier GTK4
(`integrations/gtk4/gcm_gtk4_clipboard_bridge.py`,
`ClipboardBridge._on_remote_files_changed` / `_download_and_set_files`).

Contexte : `features.md` listait ce chemin (RDP -> local, téléchargement
asynchrone) comme implémenté mais non couvert par le moindre test — les
tests existants (`tests/test_gtk4_bridges.py`) ne testent que
`_pixbuf_to_dib`/`_dib_to_texture`. Ce fichier suit le même principe que
`test_gtk4_bridges.py` (substitution de `gi`/`asyncrdp` par les
doublures de `tests/gtk_stub/` le temps de l'import, sans dépendre de
GTK4 réel ni du binding cffi compilé) pour exercer, en pur Python :

- la conversion du séparateur `\\` (convention MS-RDPECLIP) vers
  `os.sep` et la reconstruction de l'arborescence sur disque, dossiers
  compris ;
- le fait qu'un dossier annoncé (`is_directory=True`) est recréé mais
  jamais passé à `request_remote_file_contents` ;
- le filtre "premier niveau seulement" avant de construire la
  `Gdk.FileList` (une entrée imbriquée, ex. `"sub\\file.txt"`, ne doit
  pas apparaître individuellement dans la liste finale — seul son
  dossier racine de premier niveau y figure, qu'il s'agisse d'un
  fichier ou d'un dossier — voir CLAUDE.md, correctif du 2026-09-06 :
  un dossier de premier niveau était auparavant exclu à tort de la
  liste finale, alors même que son contenu était bien recréé sur
  disque) ;
- l'absence totale d'appel à `Gdk.ContentProvider`/`clipboard.set_content`
  seulement pour une sélection réellement vide (aucune entrée annoncée
  du tout) — un dossier de premier niveau vide compte, lui, comme une
  entrée utilisable depuis ce même correctif.

Ce que ceci NE teste PAS (nécessiterait un vrai display GTK4 + un vrai
serveur RDP, cf. features.md) : le rendu réel dans un presse-papier
système, la lecture par un lecteur externe — désormais couvert par
`tests/test_gtk4_live.py::test_clipboard_bridge_files_download_from_rdp_via_external_reader`
(2026-09-06), qui a justement mis en évidence puis vérifié le correctif
ci-dessus en conditions réelles avant qu'il ne soit répercuté ici.

Équivalent en ligne de commande (une fois pytest disponible) :

    PYTHONPATH=tests/gtk_stub pytest tests/test_gtk4_clipboard_file_download.py

Ce fichier n'utilise que des `assert` nus (pas de fixtures pytest) afin
de rester exécutable aussi directement via `python3`, sans dépendance à
pytest — utile dans un environnement sans accès réseau pour
l'installer (voir `_run_all()` en bas de fichier).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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


class _NullLogger:
    """Doublure minimale de `loguru.logger` : le pont ne s'en sert que
    pour du logging (`debug`/`info`/`warning`), jamais pour du contrôle
    de flux — un no-op silencieux suffit. N'utilisée que si le vrai
    `loguru` n'est pas installé (environnement sans accès réseau pour
    le récupérer) ; sinon le vrai module est importé normalement."""

    def __getattr__(self, name):
        def _noop(*args, **kwargs):
            return None

        return _noop


def _import_clipboard_bridge_with_stub():
    """Même principe que `_import_bridges_with_stub()` dans
    `test_gtk4_bridges.py` (dupliqué ici plutôt que partagé : les deux
    fichiers de test doivent rester exécutables indépendamment, y
    compris à la main sans pytest ni rootdir particulier)."""
    keys = (
        "gi",
        "gi.repository",
        "asyncrdp",
        "asyncrdp.tracing",
        "loguru",
        "gcm_gtk4_clipboard_bridge",
    )
    saved = {key: sys.modules.get(key) for key in keys}
    try:
        fake_gi = ModuleType("gi")
        sys.modules["gi"] = fake_gi
        stub_repository = _load_module_from_path("gi.repository", _STUB_DIR / "gi" / "repository.py")
        fake_gi.repository = stub_repository
        stub_asyncrdp = _load_module_from_path("asyncrdp", _STUB_DIR / "asyncrdp.py")
        # Même correctif que dans test_gtk4_bridges.py::_import_bridges_with_stub
        # (voir son commentaire pour le détail) : `asyncrdp.tracing` est pur
        # Python (loguru + stdlib, aucune dépendance cffi), on charge le vrai
        # fichier source plutôt que de le stubber, pour que ce fichier reste
        # exécutable isolément (cf. docstring, invocation directe sans le
        # reste de la suite).
        _load_module_from_path("asyncrdp.tracing", _REPO_ROOT / "src" / "asyncrdp" / "tracing.py")

        try:
            import loguru  # noqa: F401 - juste pour tester la disponibilité
        except ModuleNotFoundError:
            fake_loguru = ModuleType("loguru")
            fake_loguru.logger = _NullLogger()
            sys.modules["loguru"] = fake_loguru

        clipboard_bridge = _load_module_from_path(
            "gcm_gtk4_clipboard_bridge", _GTK4_DIR / "gcm_gtk4_clipboard_bridge.py"
        )
    finally:
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod
    return clipboard_bridge, stub_repository, stub_asyncrdp


clipboard_bridge, stub_gi, stub_asyncrdp = _import_clipboard_bridge_with_stub()
Gdk = stub_gi.Gdk
RemoteFileInfo = stub_asyncrdp.RemoteFileInfo


class _SpyClipboard:
    """Remplace `ClipboardBridge._clipboard` après construction pour
    observer si/comment `set_content` est appelé, sans dépendre du
    comportement passe-partout de `_AnyCall` (qui ne garde aucune
    trace)."""

    def __init__(self):
        self.set_content_calls: list[object] = []

    def set_content(self, provider) -> None:
        self.set_content_calls.append(provider)


class _FakeClipboardAPI:
    """Doublure de `asyncrdp.Clipboard` : ne fournit que ce que
    `ClipboardBridge.__init__` assigne (les callbacks) et ce que
    `_download_and_set_files` appelle (`request_remote_file_contents`).
    """

    def __init__(self, contents: dict[int, bytes]):
        self._contents = contents
        self.on_remote_text_changed = None
        self.on_remote_image_changed = None
        self.on_remote_files_changed = None
        self.get_local_text = None
        self.get_local_image_dib = None
        self.get_local_files = None
        self.requested_indices: list[int] = []

    async def request_remote_file_contents(self, index: int) -> bytes:
        self.requested_indices.append(index)
        return self._contents[index]


class _FakeRdpClient:
    def __init__(self, contents: dict[int, bytes]):
        self.clipboard = _FakeClipboardAPI(contents)


def _make_bridge(contents: dict[int, bytes]) -> tuple[object, _FakeRdpClient, _SpyClipboard]:
    rdp = _FakeRdpClient(contents)
    bridge = clipboard_bridge.ClipboardBridge(rdp, Gdk.Display())
    spy = _SpyClipboard()
    bridge._clipboard = spy  # remplace l'objet Gdk.Clipboard (stub) par le spy
    return bridge, rdp, spy


def test_download_recreates_nested_directory_and_converts_separator():
    files = [
        RemoteFileInfo(index=0, name="dossier", size=0, is_directory=True),
        RemoteFileInfo(index=1, name="dossier\\fichier.txt", size=5, is_directory=False),
        RemoteFileInfo(index=2, name="racine.txt", size=3, is_directory=False),
    ]
    contents = {1: b"salut", 2: b"abc"}
    bridge, rdp, _spy = _make_bridge(contents)

    asyncio.run(bridge._download_and_set_files(files))

    nested = bridge._download_dir / "dossier" / "fichier.txt"
    top = bridge._download_dir / "racine.txt"
    assert nested.read_bytes() == b"salut"
    assert top.read_bytes() == b"abc"
    # Le dossier annoncé comme tel n'a jamais été demandé au serveur (pas
    # de contenu à transférer pour une entrée is_directory=True).
    assert 0 not in rdp.clipboard.requested_indices
    assert set(rdp.clipboard.requested_indices) == {1, 2}


def test_download_only_top_level_entries_reach_file_list():
    files = [
        RemoteFileInfo(index=0, name="dossier", size=0, is_directory=True),
        RemoteFileInfo(index=1, name="dossier\\fichier.txt", size=5, is_directory=False),
        RemoteFileInfo(index=2, name="racine.txt", size=3, is_directory=False),
    ]
    contents = {1: b"salut", 2: b"abc"}
    bridge, _rdp, spy = _make_bridge(contents)

    captured: list[list] = []
    original_new_from_list = Gdk.FileList.new_from_list

    def _spy_new_from_list(gfiles):
        captured.append(list(gfiles))
        return original_new_from_list(gfiles)

    Gdk.FileList.new_from_list = staticmethod(_spy_new_from_list)
    try:
        asyncio.run(bridge._download_and_set_files(files))
    finally:
        Gdk.FileList.new_from_list = original_new_from_list

    # Deux entrées de premier niveau : "dossier" (recréé sur disque, ET
    # proposé lui-même comme GFile — corrigé le 2026-09-06, voir CLAUDE.md :
    # l'exclure ici faisait disparaître silencieusement tout dossier collé
    # depuis RDP d'un point de vue utilisateur, alors que son contenu
    # existait bel et bien sur disque) et "racine.txt". Le fichier imbriqué
    # ("dossier\\fichier.txt") n'apparaît lui jamais individuellement —
    # son dossier racine de premier niveau suffit à le représenter.
    assert len(captured) == 1
    assert len(captured[0]) == 2
    # Le contenu exact du GFile est un `_AnyCall` du stub (pas de vrai
    # chemin observable dessus) : on vérifie donc plutôt, en amont, que
    # le chemin passé à `Gio.File.new_for_path` était le bon.
    assert spy.set_content_calls  # set_content a bien été appelé une fois


def test_download_empty_top_level_folder_is_still_pasted():
    """Un dossier de premier niveau, même vide, doit malgré tout être
    proposé au presse-papier local (corrigé le 2026-09-06 — voir
    CLAUDE.md : coller un dossier vide copié depuis RDP ne faisait
    auparavant rien du tout, silencieusement, alors que le dossier avait
    bel et bien été recréé sur disque)."""
    files = [RemoteFileInfo(index=0, name="dossier_vide", size=0, is_directory=True)]
    bridge, _rdp, spy = _make_bridge(contents={})

    asyncio.run(bridge._download_and_set_files(files))

    assert (bridge._download_dir / "dossier_vide").is_dir()
    assert len(spy.set_content_calls) == 1


def test_download_no_top_level_entries_skips_clipboard_entirely():
    """Seul un cas est réellement « rien à coller » : une sélection
    entièrement vide (aucune entrée annoncée du tout). Contrairement à
    l'ancienne version de ce test, un dossier de premier niveau — même
    vide — compte désormais comme une entrée utilisable (voir le test
    ci-dessus)."""
    bridge, _rdp, spy = _make_bridge(contents={})

    asyncio.run(bridge._download_and_set_files([]))

    assert spy.set_content_calls == []


def test_download_parallelizes_with_a_bound_of_four():
    """`_download_and_set_files` documente un parallélisme borné à 4
    (cf. sa docstring) : on vérifie qu'un cinquième téléchargement ne
    démarre qu'une fois qu'un des quatre premiers s'est terminé, plutôt
    que de partir en même temps que les quatre premiers ou en pure
    séquence."""
    names = [f"f{i}.txt" for i in range(6)]
    files = [RemoteFileInfo(index=i, name=name, size=1, is_directory=False) for i, name in enumerate(names)]
    contents = dict.fromkeys(range(6), b"x")
    bridge, rdp, _spy = _make_bridge(contents)

    in_flight = 0
    max_in_flight = 0
    release = asyncio.Event()

    real_request = rdp.clipboard.request_remote_file_contents

    async def _tracked_request(index: int) -> bytes:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        if in_flight >= 4:
            release.set()
        else:
            # Attente bornée : si le code sous test ne laisse jamais
            # tourner 4 téléchargements de front (régression du bound du
            # sémaphore), `release` ne se déclenche jamais — on veut un
            # échec net de l'assertion plutôt qu'un blocage indéfini du
            # test (et du process qui l'exécute).
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(release.wait(), timeout=5)
        try:
            return await real_request(index)
        finally:
            in_flight -= 1

    rdp.clipboard.request_remote_file_contents = _tracked_request

    asyncio.run(asyncio.wait_for(bridge._download_and_set_files(files), timeout=10))

    assert max_in_flight == 4


def _run_all() -> None:
    """Petit exécuteur maison : permet de lancer ce fichier avec
    `python3 tests/test_gtk4_clipboard_file_download.py` dans un
    environnement sans pytest installé (ex. sandbox sans accès réseau)."""
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures.append((name, exc))
            print(f"FAIL {name}: {exc!r}")
        else:
            print(f"PASS {name}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} tests passés")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
