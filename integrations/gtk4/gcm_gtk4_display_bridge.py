# SPDX-License-Identifier: GPL-3.0-or-later
"""
gcm_gtk4_display_bridge.py

Widget GTK4 pour un onglet GCM RDP : affiche les frames poussées par
asyncrdp.Client.frames (BGRA32 brut) et remonte souris/clavier vers
Client.mouse / Client.keyboard.

Complète la boucle : asyncrdp.py (protocole) +
gcm_gtk4_clipboard_bridge.py (presse-papier) + ce fichier (affichage/input)
= un onglet RDP fonctionnel dans GCM.

Limitations connues (voir commentaires) : mapping clavier partiel
(caractères imprimables + touches listées dans asyncrdp_scancode_from_name),
pas de gestion du redimensionnement dynamique (résolution RDP figée à la
connexion, cf. asyncrdp_set_resolution dans asyncrdp.connect()),
mise à l'échelle souris naïve (linéaire, sans letterboxing).
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from gi.repository import Gdk, GLib, Gtk  # type: ignore
from loguru import logger

from asyncrdp import Client as RdpClient
from asyncrdp.tracing import traced

# Doit correspondre à PIXEL_FORMAT_BGRA32 côté FreeRDP (voir
# asyncrdp_gdi_init dans asyncrdp_shim.c). GTK4/GDK utilise l'ordre
# d'octets natif de la plateforme pour nommer ses formats — sur les
# machines little-endian usuelles ça correspond à B8G8R8A8, mais c'est un
# point à vérifier concrètement (frame corrompue/canaux inversés = signe
# qu'il faut ajuster ce format).
_GDK_MEMORY_FORMAT = Gdk.MemoryFormat.B8G8R8A8


class RdpView(Gtk.Widget):
    """Un onglet GCM = une instance de RdpView, montée sur un Client déjà
    connecté (voir attach())."""

    __gtype_name__ = "RdpView"

    @traced
    def __init__(self):
        super().__init__()
        self._picture = Gtk.Picture()
        self._picture.set_parent(self)
        self._picture.set_can_shrink(True)
        self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)

        self._rdp: RdpClient | None = None
        self._remote_size: tuple[int, int] = (1920, 1080)
        self._pump_task: asyncio.Task | None = None
        self._resize_timer: asyncio.TimerHandle | None = None

        self._setup_input_controllers()

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    @traced
    def attach(self, rdp_client: RdpClient) -> None:
        """À appeler une fois connect() réussi côté appelant."""
        self._rdp = rdp_client
        if rdp_client.frame_size:
            self._remote_size = rdp_client.frame_size
        self._pump_task = asyncio.ensure_future(self._pump_frames())
        logger.debug("RdpView attaché à un Client asyncrdp")

    @traced
    def detach(self) -> None:
        if self._pump_task is not None:
            self._pump_task.cancel()
            self._pump_task = None
        self._rdp = None
        logger.debug("RdpView détaché")

    @traced
    async def _pump_frames(self) -> None:
        assert self._rdp is not None
        try:
            while True:
                raw = await self._rdp.get_frame()
                self._remote_size = self._rdp.frame_size or self._remote_size
                self._show_frame(raw, self._remote_size)
        except asyncio.CancelledError as exc:
            logger.debug("boucle d'affichage annulée : {}", exc)

    @traced
    def _show_frame(self, raw: bytes, size: tuple[int, int]) -> None:
        width, height = size
        # GBytes copie/retient les données — pas de risque avec le buffer
        # `raw` déjà détaché du framebuffer C (copié dans _on_end_paint()
        # côté asyncrdp.py).
        gbytes = GLib.Bytes.new(raw)
        stride = width * 4  # BGRA32 : 4 octets/pixel, pas de padding attendu
        texture = Gdk.MemoryTexture.new(width, height, _GDK_MEMORY_FORMAT, gbytes, stride)
        self._picture.set_paintable(texture)

    # ------------------------------------------------------------------
    # Entrées : souris
    # ------------------------------------------------------------------

    @traced
    def _setup_input_controllers(self) -> None:
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        click = Gtk.GestureClick.new()
        click.set_button(0)  # tous les boutons
        click.connect("pressed", self._on_button_pressed)
        click.connect("released", self._on_button_released)
        self.add_controller(click)

        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

        key = Gtk.EventControllerKey.new()
        key.connect("key-pressed", self._on_key_pressed)
        key.connect("key-released", self._on_key_released)
        self.set_focusable(True)
        self.add_controller(key)

    @traced
    def _to_remote_coords(self, local_x: float, local_y: float) -> tuple[int, int]:
        """
        Mise à l'échelle naïve : le widget affiche le flux avec
        Gtk.ContentFit.CONTAIN (letterboxing géré par Gtk.Picture), mais
        cette conversion suppose que le widget occupe exactement la zone
        affichée — à corriger si des bandes noires apparaissent (calculer
        le rectangle réel de la vidéo dans le widget plutôt que d'utiliser
        get_width()/get_height() bruts).
        """
        widget_w, widget_h = self.get_width(), self.get_height()
        remote_w, remote_h = self._remote_size
        if widget_w == 0 or widget_h == 0:
            return 0, 0
        x = int(local_x * remote_w / widget_w)
        y = int(local_y * remote_h / widget_h)
        return max(0, min(x, remote_w - 1)), max(0, min(y, remote_h - 1))

    @traced
    def _on_motion(self, controller, x: float, y: float) -> None:
        if self._rdp is None:
            return
        rx, ry = self._to_remote_coords(x, y)
        self._rdp.mouse.move(rx, ry)

    _BUTTON_NAMES: ClassVar[dict[int, str]] = {1: "left", 2: "middle", 3: "right"}

    @traced
    def _on_button_pressed(self, gesture, n_press: int, x: float, y: float) -> None:
        if self._rdp is None:
            return
        self.grab_focus()  # pour recevoir les événements clavier ensuite
        button = self._BUTTON_NAMES.get(gesture.get_current_button(), "left")
        rx, ry = self._to_remote_coords(x, y)
        self._rdp.mouse.move(rx, ry)
        self._rdp.mouse.button_press(button)

    @traced
    def _on_button_released(self, gesture, n_press: int, x: float, y: float) -> None:
        if self._rdp is None:
            return
        button = self._BUTTON_NAMES.get(gesture.get_current_button(), "left")
        self._rdp.mouse.button_release(button)

    @traced
    def _on_scroll(self, controller, dx: float, dy: float) -> bool:
        if self._rdp is None:
            return False
        # dy > 0 = molette vers le bas en GTK4 ; asyncrdp.Mouse.scroll()
        # attend delta positif = vers le haut (cf. commentaire de la
        # méthode dans asyncrdp.py) — d'où l'inversion de signe.
        self._rdp.mouse.scroll(int(-dy * 120))
        return True

    # ------------------------------------------------------------------
    # Entrées : clavier
    # ------------------------------------------------------------------

    # Touches de contrôle GDK -> noms attendus par
    # asyncrdp_scancode_from_name() côté shim. Sous-ensemble couvert par
    # ce dernier ; à étendre en parallèle si de nouvelles touches sont
    # ajoutées côté shim.
    _CONTROL_KEYS: ClassVar[dict[int, str]] = {
        Gdk.KEY_Return: "return",
        Gdk.KEY_KP_Enter: "return",
        Gdk.KEY_Tab: "tab",
        Gdk.KEY_BackSpace: "backspace",
        Gdk.KEY_Escape: "escape",
        Gdk.KEY_Delete: "delete",
        Gdk.KEY_Insert: "insert",
        Gdk.KEY_Home: "home",
        Gdk.KEY_End: "end",
        Gdk.KEY_Page_Up: "page_up",
        Gdk.KEY_Page_Down: "page_down",
        Gdk.KEY_Up: "up",
        Gdk.KEY_Down: "down",
        Gdk.KEY_Left: "left",
        Gdk.KEY_Right: "right",
        Gdk.KEY_Shift_L: "shift",
        Gdk.KEY_Control_L: "ctrl",
        Gdk.KEY_Alt_L: "alt",
        Gdk.KEY_Caps_Lock: "capslock",
        Gdk.KEY_F1: "f1",
        Gdk.KEY_F2: "f2",
        Gdk.KEY_F3: "f3",
        Gdk.KEY_F4: "f4",
        Gdk.KEY_F5: "f5",
        Gdk.KEY_F6: "f6",
        Gdk.KEY_F7: "f7",
        Gdk.KEY_F8: "f8",
        Gdk.KEY_F9: "f9",
        Gdk.KEY_F10: "f10",
        Gdk.KEY_F11: "f11",
        Gdk.KEY_F12: "f12",
    }

    @traced
    def _on_key_pressed(self, controller, keyval: int, keycode: int, state) -> bool:
        if self._rdp is None:
            return False

        control_name = self._CONTROL_KEYS.get(keyval)
        if control_name is not None:
            try:
                self._rdp.keyboard.key_press(control_name)
            except ValueError:
                logger.warning("Touche de contrôle non supportée côté shim : {}", control_name)
            return True

        unicode_char = Gdk.keyval_to_unicode(keyval)
        if unicode_char:
            self._rdp.keyboard.write(chr(unicode_char))
            return True

        logger.trace("Touche ignorée (ni contrôle connue, ni caractère imprimable) : keyval={}", keyval)
        return False

    @traced
    def _on_key_released(self, controller, keyval: int, keycode: int, state) -> bool:
        # asyncrdp.Keyboard actuel envoie down+up immédiatement au press
        # (voir key_press()/write() dans asyncrdp.py) — donc rien
        # à faire ici pour l'instant. Point à revoir si un usage réel
        # nécessite de distinguer maintien de touche (répétition native
        # côté serveur) plutôt que rejouer down+up à chaque frappe.
        return False

    # ------------------------------------------------------------------
    # Layout GTK (Gtk.Widget composite minimal autour de Gtk.Picture)
    # ------------------------------------------------------------------

    @traced
    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        self._picture.allocate(width, height, baseline, None)
        self._on_widget_resize(width, height)

    # Debounce : un redimensionnement de fenêtre déclenche de nombreux
    # size_allocate en rafale (drag du bord de fenêtre) — on ne veut pas
    # spammer le serveur RDP d'une demande de resize par pixel.
    _RESIZE_DEBOUNCE = 0.3  # secondes

    @traced
    def _on_widget_resize(self, width: int, height: int) -> None:
        if self._rdp is None or width <= 0 or height <= 0:
            return
        if self._resize_timer is not None:
            self._resize_timer.cancel()
        loop = asyncio.get_event_loop()
        self._resize_timer = loop.call_later(self._RESIZE_DEBOUNCE, self._rdp.request_resize, width, height)

    @traced
    def do_measure(self, orientation, for_size):
        return self._picture.measure(orientation, for_size)

    @traced
    def do_dispose(self) -> None:
        self.detach()
        if self._picture is not None:
            self._picture.unparent()
            self._picture = None
