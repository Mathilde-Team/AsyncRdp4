# SPDX-License-Identifier: GPL-3.0-or-later
"""
Stub minimal de `gi.repository` pour tester la logique pure des ponts
GTK4 de asyncrdp (`integrations/gtk4/*.py`) sans GTK4 installé ni
display réel (Xvfb ou autre).

Repris du même principe que `tests/gtk_stub/` dans `pluginvnc2` (paquet
VNC frère de ce projet, même famille de ponts GCM) : un faux module qui
n'implémente QUE ce dont `gcm_gtk4_display_bridge.py` /
`gcm_gtk4_clipboard_bridge.py` ont besoin à l'import et pour exercer les
méthodes couvertes par les tests de `tests/test_gtk4_bridges.py` — pas
un mock GTK4 complet. Les objets sans logique testée (Gtk.Picture, les
contrôleurs d'event GTK, Gdk.ContentProvider...) acceptent n'importe
quel appel/attribut via `_AnyCall` et ne vérifient rien : suffisant pour
ne pas planter à l'exécution, pas pour tester du rendu réel.

`Gtk.Application`/`Gtk.ApplicationWindow` et `Gdk.Display.get_default()`
ajoutés le 2026-09-18 : nécessaires pour importer
`gcm_gtk4_demo_viewer.py` sous stub (voir
tests/test_gtk4_demo_viewer.py), qui définit `class DemoApp(Gtk.
Application)` au niveau module. Ne simulent aucune vraie boucle GLib —
suffisants pour tester `parse_args()`/`build_rdp_options()` (fonctions
pures qui n'instancient jamais DemoApp), pas pour exercer le
comportement réel de DemoApp (validé séparément avec un vrai
Gtk.Application, voir tests/test_gtk4_live.py).
"""

from __future__ import annotations


class _AnyCall:
    """Objet passe-partout : accepte n'importe quel accès d'attribut ou
    appel de méthode et renvoie un nouvel `_AnyCall`. Utilisé pour tous
    les objets GTK dont on n'a pas besoin du comportement réel ici."""

    def __getattr__(self, name):
        return _AnyCall()

    def __call__(self, *args, **kwargs):
        return _AnyCall()

    def __bool__(self):
        return True


# ---------------------------------------------------------------------
# GLib
# ---------------------------------------------------------------------


class GLib:
    PRIORITY_DEFAULT = 0

    class Error(Exception):
        """Doit exister pour les `except GLib.Error` des ponts."""

    class Bytes:
        """GLib.Bytes.new(data) — on garde juste les octets accessibles
        pour inspection dans les tests (`.data`)."""

        def __init__(self, data: bytes):
            self.data = bytes(data)

        @staticmethod
        def new(data: bytes) -> GLib.Bytes:
            return GLib.Bytes(data)

        def __len__(self):
            return len(self.data)


# ---------------------------------------------------------------------
# Gtk
# ---------------------------------------------------------------------


class ContentFit:
    CONTAIN = "contain"


class Widget:
    """Base minimale pour que `RdpView(Gtk.Widget)` s'instancie sans
    GTK4 réel. Les tests fixent `_stub_width`/`_stub_height` sur
    l'instance pour piloter `get_width()`/`get_height()`."""

    def __init__(self, *args, **kwargs):
        pass

    def add_controller(self, *args, **kwargs):
        pass

    def set_focusable(self, *args, **kwargs):
        pass

    def grab_focus(self, *args, **kwargs):
        pass

    def get_width(self) -> int:
        return getattr(self, "_stub_width", 0)

    def get_height(self) -> int:
        return getattr(self, "_stub_height", 0)


class Picture(_AnyCall):
    def measure(self, orientation, for_size):
        return (0, 0, -1, -1)


class EventControllerScrollFlags:
    VERTICAL = 1


class EventControllerMotion(_AnyCall):
    @staticmethod
    def new() -> EventControllerMotion:
        return EventControllerMotion()


class GestureClick(_AnyCall):
    def __init__(self):
        self._current_button = 1

    @staticmethod
    def new() -> GestureClick:
        return GestureClick()

    def get_current_button(self) -> int:
        return self._current_button


class EventControllerScroll(_AnyCall):
    @staticmethod
    def new(flags) -> EventControllerScroll:
        return EventControllerScroll()


class EventControllerKey(_AnyCall):
    @staticmethod
    def new() -> EventControllerKey:
        return EventControllerKey()


class Application(_AnyCall):
    """Stub minimal ajouté le 2026-09-18 pour importer
    gcm_gtk4_demo_viewer.py (class DemoApp(Gtk.Application)) sous stub —
    ne simule PAS de vraie boucle GLib (`run()`/`connect()` n'ont aucun
    effet réel). Suffisant pour tester parse_args()/build_rdp_options()
    (fonctions pures, n'instancient jamais DemoApp) ; le comportement réel
    de DemoApp est validé séparément avec un vrai Gtk.Application, voir
    tests/test_gtk4_live.py."""

    def __init__(self, *args, **kwargs):
        pass


class ApplicationWindow(_AnyCall):
    def __init__(self, *args, **kwargs):
        pass


class Gtk:
    Widget = Widget
    Picture = Picture
    ContentFit = ContentFit
    EventControllerMotion = EventControllerMotion
    GestureClick = GestureClick
    EventControllerScrollFlags = EventControllerScrollFlags
    EventControllerScroll = EventControllerScroll
    EventControllerKey = EventControllerKey
    Application = Application
    ApplicationWindow = ApplicationWindow


# ---------------------------------------------------------------------
# Gdk
# ---------------------------------------------------------------------


class MemoryFormat:
    B8G8R8A8 = "B8G8R8A8"


class MemoryTexture:
    """Capture les paramètres du dernier appel à `.new()` pour
    inspection dans les tests (au lieu de vraiment créer une texture)."""

    last_call: tuple | None = None

    @staticmethod
    def new(width, height, fmt, gbytes, stride) -> _AnyCall:
        MemoryTexture.last_call = (width, height, fmt, gbytes.data, stride)
        return _AnyCall()


class Texture(_AnyCall):
    @staticmethod
    def new_for_pixbuf(pixbuf) -> _AnyCall:
        return _AnyCall()


class ContentProvider:
    @staticmethod
    def new_for_value(value) -> _AnyCall:
        return _AnyCall()


class FileList(_AnyCall):
    @staticmethod
    def new_from_list(gfiles) -> _AnyCall:
        return _AnyCall()


class Clipboard(_AnyCall):
    pass


class Display(_AnyCall):
    def get_clipboard(self) -> Clipboard:
        return Clipboard()

    @staticmethod
    def get_default() -> Display:
        return Display()


# Sous-ensemble des keyvals GDK réellement mappés dans
# gcm_gtk4_display_bridge._CONTROL_KEYS (valeurs = vrais keysyms X11,
# comme exposés par gi.repository.Gdk sur un système réel — cf.
# /usr/include/gdk-3.0 ou gdk/gdkkeysyms.h).
KEY_Return = 0xFF0D
KEY_KP_Enter = 0xFF8D
KEY_Tab = 0xFF09
KEY_BackSpace = 0xFF08
KEY_Escape = 0xFF1B
KEY_Delete = 0xFFFF
KEY_Insert = 0xFF63
KEY_Home = 0xFF50
KEY_End = 0xFF57
KEY_Page_Up = 0xFF55
KEY_Page_Down = 0xFF56
KEY_Up = 0xFF52
KEY_Down = 0xFF54
KEY_Left = 0xFF51
KEY_Right = 0xFF53
KEY_Shift_L = 0xFFE1
KEY_Control_L = 0xFFE3
KEY_Alt_L = 0xFFE9
KEY_Caps_Lock = 0xFFE5
KEY_F1, KEY_F2, KEY_F3, KEY_F4 = 0xFFBE, 0xFFBF, 0xFFC0, 0xFFC1
KEY_F5, KEY_F6, KEY_F7, KEY_F8 = 0xFFC2, 0xFFC3, 0xFFC4, 0xFFC5
KEY_F9, KEY_F10, KEY_F11, KEY_F12 = 0xFFC6, 0xFFC7, 0xFFC8, 0xFFC9


def keyval_to_unicode(keyval: int) -> int:
    """Approximation suffisante pour les tests : pour les touches
    Latin-1 imprimables (0x20-0x7e), le keyval GDK réel est déjà égal au
    codepoint Unicode — c'est le cas qu'on veut exercer
    (`RdpView._on_key_pressed` -> `keyboard.write(chr(...))`). Les
    autres keyvals (touches de contrôle, F-keys...) renvoient 0 comme le
    ferait la vraie fonction."""
    if 0x20 <= keyval <= 0x7E:
        return keyval
    return 0


class Gdk:
    MemoryFormat = MemoryFormat
    MemoryTexture = MemoryTexture
    Texture = Texture
    ContentProvider = ContentProvider
    FileList = FileList
    Clipboard = Clipboard
    Display = Display
    keyval_to_unicode = staticmethod(keyval_to_unicode)

    KEY_Return = KEY_Return
    KEY_KP_Enter = KEY_KP_Enter
    KEY_Tab = KEY_Tab
    KEY_BackSpace = KEY_BackSpace
    KEY_Escape = KEY_Escape
    KEY_Delete = KEY_Delete
    KEY_Insert = KEY_Insert
    KEY_Home = KEY_Home
    KEY_End = KEY_End
    KEY_Page_Up = KEY_Page_Up
    KEY_Page_Down = KEY_Page_Down
    KEY_Up = KEY_Up
    KEY_Down = KEY_Down
    KEY_Left = KEY_Left
    KEY_Right = KEY_Right
    KEY_Shift_L = KEY_Shift_L
    KEY_Control_L = KEY_Control_L
    KEY_Alt_L = KEY_Alt_L
    KEY_Caps_Lock = KEY_Caps_Lock
    KEY_F1, KEY_F2, KEY_F3, KEY_F4 = KEY_F1, KEY_F2, KEY_F3, KEY_F4
    KEY_F5, KEY_F6, KEY_F7, KEY_F8 = KEY_F5, KEY_F6, KEY_F7, KEY_F8
    KEY_F9, KEY_F10, KEY_F11, KEY_F12 = KEY_F9, KEY_F10, KEY_F11, KEY_F12


def pixbuf_get_from_texture(texture) -> _AnyCall:
    return _AnyCall()


Gdk.pixbuf_get_from_texture = staticmethod(pixbuf_get_from_texture)


# ---------------------------------------------------------------------
# GdkPixbuf
# ---------------------------------------------------------------------


class PixbufLoader:
    """Ne décode RIEN réellement (pas de vrai décodeur BMP dans le
    stub) — capture juste les octets écrits (`last_written`) pour que
    les tests puissent relire le BITMAPFILEHEADER produit par
    `_dib_to_texture()` et vérifier `bfOffBits` sans dépendre de
    gdk-pixbuf. `get_pixbuf()` renvoie un objet non-None pour simuler un
    décodage réussi (le code des ponts ne teste que `is None`)."""

    last_written: bytes | None = None

    def __init__(self):
        self._buf = bytearray()

    @classmethod
    def new_with_type(cls, type_: str) -> PixbufLoader:
        return cls()

    def write(self, data: bytes) -> None:
        self._buf.extend(data)
        PixbufLoader.last_written = bytes(self._buf)

    def close(self) -> None:
        pass

    def get_pixbuf(self) -> _AnyCall:
        return _AnyCall()


class Pixbuf(_AnyCall):
    pass


class GdkPixbuf:
    PixbufLoader = PixbufLoader
    Pixbuf = Pixbuf


# ---------------------------------------------------------------------
# Gio
# ---------------------------------------------------------------------


class File(_AnyCall):
    @staticmethod
    def new_for_path(path: str) -> _AnyCall:
        return _AnyCall()


class Gio:
    File = File


__all__ = ["GLib", "Gdk", "GdkPixbuf", "Gio", "Gtk"]
