# SPDX-License-Identifier: GPL-3.0-or-later
"""
Faux module `asyncrdp` pour `tests/gtk_stub/` — évite de dépendre du
binding cffi compilé (`_asyncrdp_cffi`, nécessite FreeRDP3 installé +
`build_ffi.py` exécuté) pour importer les ponts GTK4
(`integrations/gtk4/*.py`), qui n'ont besoin que des NOMS `Client` et
`RemoteFileInfo` à l'import (type hints / `isinstance` éventuels), pas
du binding réel.

Même principe que le faux module protocole utilisé par `pluginvnc2`
pour ses propres tests (`asyncvnc2` chez eux).

`RdpOptions`/`DriveMapping`/`FreeRDPError`/`connect` ajoutés le
2026-09-18 pour importer `gcm_gtk4_demo_viewer.py` sous stub (voir
tests/test_gtk4_demo_viewer.py) — nécessaires à l'import du module
(`build_rdp_options()` construit un `RdpOptions` réel, `DemoApp.__init__`
référence `asyncrdp.connect` comme valeur par défaut, `DemoApp._connect`
capture `asyncrdp.FreeRDPError`), jamais au binding cffi réel. `connect`
n'est jamais appelée dans les tests de logique pure (RdpSession/DemoApp y
reçoivent toujours un `connect_fn` explicite, cf. tests/test_gtk4_demo_
viewer.py) — elle n'existe ici que pour que `asyncrdp.connect` reste un
attribut valide.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class Client:
    """Stub vide : les tests de logique pure construisent leurs propres
    doublures (mouse/keyboard/clipboard factices) plutôt que
    d'instancier ce Client — cette classe n'existe que pour que
    `from asyncrdp import Client as RdpClient` fonctionne comme import
    de type."""


@dataclass
class RemoteFileInfo:
    """Doit rester en phase avec `asyncrdp._core.RemoteFileInfo` (voir
    src/asyncrdp/_core.py) — seuls les champs utilisés par
    gcm_gtk4_clipboard_bridge.py."""

    index: int
    name: str
    size: int
    is_directory: bool


@dataclass
class DriveMapping:
    """Doit rester en phase avec `asyncrdp._core.DriveMapping` (voir
    src/asyncrdp/_core.py) — seuls les deux champs qui existent réellement
    (le type réel n'en a pas d'autres)."""

    name: str
    path: str


@dataclass
class RdpOptions:
    """Doublure PARTIELLE de `asyncrdp._core.RdpOptions` — seuls les champs
    que `gcm_gtk4_demo_viewer.build_rdp_options()` renseigne réellement
    (voir ce fichier). Ne couvre pas le reste de la vraie surface de
    RdpOptions (moniteurs, RD Gateway, RemoteApp...) : pas nécessaire ici,
    ce stub sert uniquement à tester ce mapping CLI -> options précis."""

    width: int = 1920
    height: int = 1080
    redirect_clipboard: bool = True
    redirect_drives: bool = False
    drives: list[DriveMapping] = field(default_factory=list)
    ignore_certificate: bool = False


class FreeRDPError(Exception):
    """Doit exister pour le `except asyncrdp.FreeRDPError` de
    gcm_gtk4_demo_viewer.py."""


def connect(*args, **kwargs):
    """Ne doit jamais être appelée dans les tests de logique pure —
    RdpSession/DemoApp y reçoivent toujours un `connect_fn` explicite
    (voir tests/test_gtk4_demo_viewer.py). N'existe que pour que
    `asyncrdp.connect` soit un attribut valide (valeur par défaut de
    `DemoApp.__init__`)."""
    raise NotImplementedError("stub: asyncrdp.connect ne doit pas être appelée dans les tests de logique pure")


__all__ = ["Client", "DriveMapping", "FreeRDPError", "RdpOptions", "RemoteFileInfo", "connect"]
