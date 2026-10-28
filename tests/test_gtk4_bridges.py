# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_gtk4_bridges.py — logique pure des ponts GTK4
(`integrations/gtk4/gcm_gtk4_display_bridge.py` et
`gcm_gtk4_clipboard_bridge.py`), sans GTK4 installé ni display réel.

Contexte : voir CLAUDE.md ("Piste pour tester les ponts GTK4 sans
environnement graphique", 2026-09-01) et l'entrée haute priorité de
features.md. Les deux modules testés ici importent `gi.repository`
(GTK4) et `asyncrdp` (le binding cffi compilé, nécessite FreeRDP3) au
niveau module — impossible de les importer tels quels dans un sandbox
sans GTK4 réel ni FreeRDP compilé. On substitue temporairement
`sys.modules['gi']`, `sys.modules['gi.repository']` et
`sys.modules['asyncrdp']` par les doublures de `tests/gtk_stub/` (même
principe que celui déjà vérifié fonctionnel par `pluginvnc2`, paquet
VNC frère de ce projet — voir son propre `tests/gtk_stub/`) le temps de
l'import, puis on restaure l'état précédent : les modules de pont déjà
importés gardent leurs références directes vers les objets du stub, ce
qui ne perturbe pas le reste de la suite (qui a besoin du vrai
`asyncrdp` compilé — cf. conftest.py).

Ce que ceci NE teste PAS (nécessiterait un vrai display GTK4, cf.
features.md) : rendu réel à l'écran, vrai décodage BMP par gdk-pixbuf,
vrai presse-papier système. Ce que ceci teste : la logique Python pure
qui ne dépend que du calcul — mise à l'échelle souris, mapping
clavier/souris, et surtout le calcul de `bfOffBits` dans
`_dib_to_texture()` (régression du bug de palette documenté dans
CLAUDE.md, trouvé en testant contre un vrai serveur RDP).

Équivalent en ligne de commande, dans le même esprit que la commande
documentée pour `pluginvnc2` (mais ce fichier n'en a pas besoin — voir
`_import_bridges_with_stub()` ci-dessous) :

    PYTHONPATH=tests/gtk_stub pytest tests/test_gtk4_bridges.py
"""

from __future__ import annotations

import importlib.util
import struct
import sys
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


def _import_bridges_with_stub():
    """Importe les deux ponts GTK4 avec `gi`/`asyncrdp` remplacés par
    les doublures de `gtk_stub/`, puis restaure `sys.modules` pour ne
    pas perturber les autres fichiers de tests (qui ont besoin du vrai
    `asyncrdp` compilé, cf. conftest.py)."""
    keys = (
        "gi",
        "gi.repository",
        "asyncrdp",
        "asyncrdp.tracing",
        "gcm_gtk4_display_bridge",
        "gcm_gtk4_clipboard_bridge",
    )
    saved = {key: sys.modules.get(key) for key in keys}
    try:
        fake_gi = ModuleType("gi")
        sys.modules["gi"] = fake_gi
        stub_repository = _load_module_from_path("gi.repository", _STUB_DIR / "gi" / "repository.py")
        fake_gi.repository = stub_repository
        _load_module_from_path("asyncrdp", _STUB_DIR / "asyncrdp.py")
        # asyncrdp.tracing (décorateur `traced`, ajouté le 2026-09-06) n'a
        # aucune dépendance au binding cffi compilé — seulement loguru + la
        # stdlib. Le stub `asyncrdp.py` ci-dessus n'est qu'un simple module
        # (pas un paquet), donc `from asyncrdp.tracing import traced` dans
        # les deux ponts échoue sans cette étape (`ModuleNotFoundError:
        # 'asyncrdp' is not a package`) — reproductible en lançant ce
        # fichier seul, cf. commande documentée plus haut. On charge le
        # vrai fichier source plutôt que d'en réécrire une doublure, pour
        # ne pas faire diverger le comportement testé de `test_tracing.py`
        # de celui réellement appliqué aux ponts.
        _load_module_from_path("asyncrdp.tracing", _REPO_ROOT / "src" / "asyncrdp" / "tracing.py")

        display_bridge = _load_module_from_path("gcm_gtk4_display_bridge", _GTK4_DIR / "gcm_gtk4_display_bridge.py")
        clipboard_bridge = _load_module_from_path(
            "gcm_gtk4_clipboard_bridge", _GTK4_DIR / "gcm_gtk4_clipboard_bridge.py"
        )
    finally:
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod
    return display_bridge, clipboard_bridge, stub_repository


display_bridge, clipboard_bridge, stub_gi = _import_bridges_with_stub()
Gdk = stub_gi.Gdk


class _FakeMouse:
    def __init__(self):
        self.calls: list[tuple] = []

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def button_press(self, button):
        self.calls.append(("button_press", button))

    def button_release(self, button):
        self.calls.append(("button_release", button))

    def scroll(self, delta):
        self.calls.append(("scroll", delta))


class _FakeKeyboard:
    def __init__(self, raise_on: set[str] | None = None):
        self.calls: list[tuple] = []
        self._raise_on = raise_on or set()

    def key_press(self, name):
        if name in self._raise_on:
            raise ValueError(f"touche non supportée par le shim : {name}")
        self.calls.append(("key_press", name))

    def write(self, text):
        self.calls.append(("write", text))


class _FakeRdpClient:
    def __init__(self):
        self.mouse = _FakeMouse()
        self.keyboard = _FakeKeyboard()


class _FakeGesture:
    def __init__(self, button: int):
        self._button = button

    def get_current_button(self):
        return self._button


def _make_view(width: int, height: int, remote_size: tuple[int, int] = (1920, 1080)):
    view = display_bridge.RdpView()
    view._stub_width = width
    view._stub_height = height
    view._remote_size = remote_size
    view._rdp = _FakeRdpClient()
    return view


# ---------------------------------------------------------------------
# Souris : mise à l'échelle des coordonnées (_to_remote_coords)
# ---------------------------------------------------------------------


def test_to_remote_coords_scales_proportionally():
    view = _make_view(width=800, height=600, remote_size=(1920, 1080))
    assert view._to_remote_coords(400, 300) == (960, 540)


def test_to_remote_coords_clamps_to_bounds():
    view = _make_view(width=800, height=600, remote_size=(1920, 1080))
    # Une coordonnée locale au bord max ne doit jamais renvoyer
    # remote_w/remote_h (hors bornes, index pixel invalide) mais
    # remote_w - 1 / remote_h - 1.
    assert view._to_remote_coords(800, 600) == (1919, 1079)


def test_to_remote_coords_widget_not_yet_sized_returns_origin():
    view = _make_view(width=0, height=0)
    assert view._to_remote_coords(123, 456) == (0, 0)


def test_on_motion_moves_mouse_in_remote_coords():
    view = _make_view(width=800, height=600, remote_size=(1920, 1080))
    view._on_motion(controller=None, x=400.0, y=300.0)
    assert view._rdp.mouse.calls == [("move", 960, 540)]


# ---------------------------------------------------------------------
# Souris : boutons
# ---------------------------------------------------------------------


def test_button_names_mapping():
    assert display_bridge.RdpView._BUTTON_NAMES == {1: "left", 2: "middle", 3: "right"}


def test_on_button_pressed_moves_then_presses_mapped_button():
    view = _make_view(width=800, height=600, remote_size=(1920, 1080))
    view._on_button_pressed(_FakeGesture(button=3), n_press=1, x=400.0, y=300.0)
    assert view._rdp.mouse.calls == [("move", 960, 540), ("button_press", "right")]


def test_on_button_released_releases_mapped_button():
    view = _make_view(width=800, height=600)
    view._on_button_released(_FakeGesture(button=2), n_press=1, x=0.0, y=0.0)
    assert view._rdp.mouse.calls == [("button_release", "middle")]


def test_on_button_pressed_unknown_button_defaults_to_left():
    view = _make_view(width=800, height=600, remote_size=(1920, 1080))
    view._on_button_pressed(_FakeGesture(button=99), n_press=1, x=0.0, y=0.0)
    assert ("button_press", "left") in view._rdp.mouse.calls


# ---------------------------------------------------------------------
# Souris : molette (inversion de signe, cf. commentaire de _on_scroll)
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "dy,expected_delta",
    [(1.0, -120), (-1.0, 120), (2.0, -240), (0.0, 0)],
)
def test_on_scroll_inverts_sign(dy, expected_delta):
    view = _make_view(width=800, height=600)
    handled = view._on_scroll(controller=None, dx=0.0, dy=dy)
    assert handled is True
    assert view._rdp.mouse.calls == [("scroll", expected_delta)]


# ---------------------------------------------------------------------
# Clavier : touches de contrôle et caractères imprimables
# ---------------------------------------------------------------------


def test_control_keys_mapping_spot_check():
    keys = display_bridge.RdpView._CONTROL_KEYS
    assert keys[Gdk.KEY_Return] == "return"
    assert keys[Gdk.KEY_KP_Enter] == "return"
    assert keys[Gdk.KEY_F5] == "f5"
    assert keys[Gdk.KEY_Shift_L] == "shift"
    assert keys[Gdk.KEY_Left] == "left"


def test_on_key_pressed_control_key_calls_key_press():
    view = _make_view(width=800, height=600)
    handled = view._on_key_pressed(controller=None, keyval=Gdk.KEY_Return, keycode=0, state=0)
    assert handled is True
    assert view._rdp.keyboard.calls == [("key_press", "return")]


def test_on_key_pressed_printable_char_calls_write():
    view = _make_view(width=800, height=600)
    handled = view._on_key_pressed(controller=None, keyval=ord("a"), keycode=0, state=0)
    assert handled is True
    assert view._rdp.keyboard.calls == [("write", "a")]


def test_on_key_pressed_unhandled_key_returns_false():
    view = _make_view(width=800, height=600)
    # Hors plage ASCII imprimable et absent de _CONTROL_KEYS : ni
    # touche de contrôle connue, ni caractère imprimable (ex. une
    # touche multimédia).
    handled = view._on_key_pressed(controller=None, keyval=0x1008FF12, keycode=0, state=0)
    assert handled is False
    assert view._rdp.keyboard.calls == []


def test_on_key_pressed_control_key_unsupported_by_shim_is_caught():
    view = _make_view(width=800, height=600)
    view._rdp.keyboard = _FakeKeyboard(raise_on={"return"})
    # Le pont doit avaler le ValueError levé par le shim (touche listée
    # côté GDK mais pas dans asyncrdp_scancode_from_name) et quand même
    # signaler l'event comme géré, plutôt que de laisser planter GTK4.
    handled = view._on_key_pressed(controller=None, keyval=Gdk.KEY_Return, keycode=0, state=0)
    assert handled is True
    assert view._rdp.keyboard.calls == []


def test_on_key_released_is_a_noop():
    view = _make_view(width=800, height=600)
    handled = view._on_key_released(controller=None, keyval=Gdk.KEY_Return, keycode=0, state=0)
    assert handled is False
    assert view._rdp.keyboard.calls == []


# ---------------------------------------------------------------------
# Presse-papier image : _dib_to_texture / bfOffBits
#
# Régression directe du bug documenté dans CLAUDE.md ("Bug de calcul
# (clipboard image)") : bfOffBits doit inclure la table de couleurs
# pour les images en couleurs indexées (<=8bpp), sans quoi le fichier
# BMP reconstruit a un offset de pixels faux.
# ---------------------------------------------------------------------


def _make_dib(bit_count: int, colors_used: int, pixel_bytes: bytes) -> bytes:
    palette = b"\x00" * (4 * (colors_used or (1 << bit_count if bit_count <= 8 else 0)))
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        1,
        1,
        1,
        bit_count,
        0,
        len(pixel_bytes),
        0,
        0,
        colors_used,
        0,
    )
    return header + palette + pixel_bytes


def _bfoffbits_of(bmp_bytes: bytes) -> int:
    (offset,) = struct.unpack_from("<I", bmp_bytes, 10)
    return offset


def test_dib_to_texture_24bpp_no_palette_offset():
    dib = _make_dib(bit_count=24, colors_used=0, pixel_bytes=b"\x00" * 4)
    clipboard_bridge._dib_to_texture(dib)
    assert _bfoffbits_of(stub_gi.GdkPixbuf.PixbufLoader.last_written) == 14 + 40


def test_dib_to_texture_indexed_4bpp_includes_palette_in_offset():
    # 4bpp avec biClrUsed explicite = 16 (palette max pour 4bpp).
    dib = _make_dib(bit_count=4, colors_used=16, pixel_bytes=b"\x00" * 4)
    clipboard_bridge._dib_to_texture(dib)
    expected = 14 + 40 + 16 * 4  # header + BITMAPINFOHEADER + palette RGBQUAD
    assert _bfoffbits_of(stub_gi.GdkPixbuf.PixbufLoader.last_written) == expected


def test_dib_to_texture_indexed_1bpp_biclrused_zero_defaults_to_full_palette():
    # biClrUsed == 0 est une convention Win32 signifiant "toute la
    # palette possible pour cette profondeur" (2^bit_count), pas 0
    # couleur — cf. le `if colors_used else (1 << bit_count)` du code.
    dib = _make_dib(bit_count=1, colors_used=0, pixel_bytes=b"\x00" * 4)
    clipboard_bridge._dib_to_texture(dib)
    expected = 14 + 40 + 2 * 4  # 2^1 = 2 couleurs
    assert _bfoffbits_of(stub_gi.GdkPixbuf.PixbufLoader.last_written) == expected


def test_dib_to_texture_8bpp_partial_palette_uses_biclrused_not_max():
    # 8bpp avec une palette réduite (biClrUsed=20 < 256) : l'offset doit
    # suivre biClrUsed réel, pas le maximum théorique de la profondeur.
    dib = _make_dib(bit_count=8, colors_used=20, pixel_bytes=b"\x00" * 4)
    clipboard_bridge._dib_to_texture(dib)
    expected = 14 + 40 + 20 * 4
    assert _bfoffbits_of(stub_gi.GdkPixbuf.PixbufLoader.last_written) == expected


def test_dib_to_texture_too_short_returns_none():
    assert clipboard_bridge._dib_to_texture(b"\x00\x00") is None


# ---------------------------------------------------------------------
# Presse-papier image : _pixbuf_to_dib (conversion RGB->BGR, bottom-up,
# padding de fin de ligne sur 4 octets)
# ---------------------------------------------------------------------


class _FakePixbuf:
    """Doublure minimale d'un GdkPixbuf.Pixbuf : image RGB sans canal
    alpha (n_channels=3), rowstride == width*n_channels (pas de padding
    dans le pixbuf source lui-même — GdkPixbuf peut en avoir, mais ce
    n'est pas ce qu'on veut exercer ici, c'est le padding *produit*
    côté DIB qui nous intéresse)."""

    def __init__(self, width: int, height: int, rows_rgb: list[bytes]):
        self._width = width
        self._height = height
        self._pixels = b"".join(rows_rgb)

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height

    def get_n_channels(self):
        return 3

    def get_rowstride(self):
        return self._width * 3

    def get_pixels(self):
        return self._pixels


def test_pixbuf_to_dib_header_fields():
    # 3x2, une couleur par pixel, RGB pur.
    top_row = bytes([255, 0, 0]) + bytes([0, 255, 0]) + bytes([0, 0, 255])
    bottom_row = bytes([10, 20, 30]) + bytes([40, 50, 60]) + bytes([70, 80, 90])
    pixbuf = _FakePixbuf(width=3, height=2, rows_rgb=[top_row, bottom_row])

    dib = clipboard_bridge._pixbuf_to_dib(pixbuf)

    (size, width, height, planes, bit_count, compression, size_image, _xppm, _yppm, _clr_used, _clr_important) = (
        struct.unpack_from("<IiiHHIIiiII", dib, 0)
    )
    assert (size, width, height, planes, bit_count, compression) == (40, 3, 2, 1, 24, 0)
    row_size = ((3 * 3 + 3) // 4) * 4  # 12 : padding de 3 octets par ligne
    assert size_image == row_size * 2
    assert len(dib) == 40 + row_size * 2


def test_pixbuf_to_dib_is_bottom_up_and_bgr_ordered():
    top_row = bytes([255, 0, 0]) + bytes([0, 255, 0]) + bytes([0, 0, 255])
    bottom_row = bytes([10, 20, 30]) + bytes([40, 50, 60]) + bytes([70, 80, 90])
    pixbuf = _FakePixbuf(width=3, height=2, rows_rgb=[top_row, bottom_row])

    dib = clipboard_bridge._pixbuf_to_dib(pixbuf)
    row_size = 12
    first_body_row = dib[40 : 40 + row_size]
    second_body_row = dib[40 + row_size : 40 + 2 * row_size]

    # DIB bottom-up : la première ligne stockée est la ligne DU BAS de
    # l'image (bottom_row), et RGB -> BGR.
    assert first_body_row[0:9] == bytes([30, 20, 10, 60, 50, 40, 90, 80, 70])
    assert second_body_row[0:9] == bytes([0, 0, 255, 0, 255, 0, 255, 0, 0])


def test_pixbuf_to_dib_row_padding_is_zero_filled():
    top_row = bytes([1, 2, 3]) + bytes([4, 5, 6]) + bytes([7, 8, 9])
    bottom_row = bytes([9, 8, 7]) + bytes([6, 5, 4]) + bytes([3, 2, 1])
    pixbuf = _FakePixbuf(width=3, height=2, rows_rgb=[top_row, bottom_row])

    dib = clipboard_bridge._pixbuf_to_dib(pixbuf)
    row_size = 12
    first_body_row = dib[40 : 40 + row_size]
    second_body_row = dib[40 + row_size : 40 + 2 * row_size]

    assert first_body_row[9:12] == b"\x00\x00\x00"
    assert second_body_row[9:12] == b"\x00\x00\x00"
