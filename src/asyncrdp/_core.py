# SPDX-License-Identifier: GPL-3.0-or-later
"""
asyncrdp — binding asyncio sur libfreerdp3 via cffi (mode API).

Équivalent RDP d'asyncvnc2 pour GCM :
    async with asyncrdp.connect(host, port, username, password) as client:
        client.keyboard.write("hello")

Tout passe par le module compilé _asyncrdp_cffi (voir asyncrdp_shim.c /
build_ffi.py) — une seule frontière cffi, un seul ffi, pas de dlopen
ABI-mode parallèle.

Couvre : rdpSettings complets (écran, sécurité, profil de performance),
fds pollables intégrés à asyncio, GDI/frames, clipboard riche bidirectionnel
(texte/image/fichiers), clavier/souris, resize dynamique, redirections
locales (disques, imprimantes fines, smartcards, série, parallèle, USB,
audio, multi-écran) via les plugins de canaux natifs de FreeRDP.

Testé contre un vrai serveur RDP (xrdp) — pas seulement relu : connexion,
frames, input, resize, clipboard (texte/image/fichiers, les deux sens),
disque redirigé (lecture/écriture réelles), multi-écran. Voir les notes
d'implémentation en bas de ce fichier pour le détail des bugs trouvés et
corrigés, et ce qui reste non testé (USB, audio en conditions réelles,
imprimante/série/parallèle — bloqués par le serveur de test utilisé, pas
par le code client ; voir README.md et CLAUDE.md du projet).
"""

from __future__ import annotations

import asyncio
import os
import struct
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from loguru import logger

from asyncrdp._asyncrdp_cffi import ffi
from asyncrdp._asyncrdp_cffi import lib as shim

from .tracing import traced


class FreeRDPError(Exception):
    """Erreur remontée depuis libfreerdp (code + message)."""


@dataclass
class DriveMapping:
    name: str  # nom vu côté serveur (ex: "home")
    path: str  # chemin local (ex: "/home/mathilde")


@dataclass
class PrinterMapping:
    """
    Redirection d'une imprimante locale vers la session distante.

    Premier serveur RDP à accepter réellement cette annonce, constaté le
    2026-09-16 (voir docs/sessions/session-29.md) : le canal serveur
    RDPDR générique de FreeRDP répond STATUS_SUCCESS, là où xrdp répond
    STATUS_NOT_SUPPORTED depuis le début du projet. L'annonce est donc
    protocolairement correcte côté client. Ce qu'aucun serveur FOSS ne
    fait encore : consommer le job d'impression lui-même — l'API
    publique RdpdrServerContext n'expose d'entrées d'E/S que pour
    Drive* et Smartcard*, rien pour l'imprimante.
    """

    name: str
    driver: str | None = None
    is_default: bool = False


@dataclass
class SerialMapping:
    """
    Redirection d'un port série local vers la session distante.

    Attention, limitation constatée EN CONDITIONS RÉELLES le 2026-09-16
    (voir docs/sessions/session-29.md) : le canal client `serial` de
    FreeRDP écrit le nom du périphérique (`name` + NUL) dans le champ
    DeviceData de l'annonce MS-RDPEFS, alors que le canal SERVEUR de
    FreeRDP exige DeviceDataLength == 0 pour ce type et répond
    ERROR_INVALID_DATA sinon. Les deux moitiés de FreeRDP sont donc en
    désaccord sur le format d'annonce, et un serveur bâti sur ce canal
    rejette la redirection quoi que fasse l'appelant. Rien à corriger
    ici : ce champ est rempli par le plugin de canal FreeRDP chargé via
    freerdp_client_load_addins(), jamais par asyncrdp.
    """

    name: str  # nom vu côté serveur (ex: "COM1")
    path: str  # périphérique local (ex: "/dev/ttyS0")


@dataclass
class ParallelMapping:
    """
    Redirection d'un port parallèle local vers la session distante.

    Même limitation que SerialMapping ci-dessus, constatée dans la même
    session du 2026-09-16 (docs/sessions/session-29.md) : DeviceData non
    vide côté client FreeRDP, refusé par le canal serveur FreeRDP avec
    ERROR_INVALID_DATA.
    """

    name: str  # nom vu côté serveur (ex: "LPT1")
    path: str  # périphérique local (ex: "/dev/lp0")


@dataclass
class MonitorDef:
    x: int
    y: int
    width: int
    height: int
    is_primary: bool = False


@dataclass
class GatewayOptions:
    hostname: str
    port: int = 443
    username: str | None = None
    password: str | None = None
    domain: str | None = None
    # Valeurs numériques documentées par xfreerdp, pas de macro publique
    # trouvée dans les headers installés (voir asyncrdp_set_gateway côté
    # shim) : 0=jamais, 1=toujours, 2=détection, 3=défaut, 4=détection sans
    # passerelle.
    usage_method: int = 1


@dataclass
class RemoteAppOptions:
    program: str  # ex: "||notepad" ou chemin complet côté serveur
    name: str | None = None
    cmdline: str | None = None


@dataclass
class RdpOptions:
    """
    Regroupe les réglages qu'on retrouve dans les onglets de mstsc
    (Écran / Ressources locales / Expérience), pour piloter la session
    RDP sans avoir à connaître les noms de champs FreeRDP sous-jacents.

    Tous les champs ont un défaut raisonnable ; ne préciser que ce qui
    doit changer.
    """

    # --- Écran ---
    width: int = 1920
    height: int = 1080
    color_depth: int = 32

    # --- Sécurité ---
    enable_nla: bool = True
    enable_tls: bool = True
    ignore_certificate: bool = False

    # --- Ressources locales ---
    redirect_clipboard: bool = True
    # redirect_printers=True redirige TOUTES les imprimantes locales
    # détectées (comportement "tout" de mstsc). Pour choisir précisément
    # lesquelles, laisser redirect_printers=False et peupler `printers` —
    # dans ce cas seules celles-ci sont redirigées.
    redirect_printers: bool = False
    printers: list[PrinterMapping] = field(default_factory=list)
    redirect_smartcards: bool = False
    redirect_drives: bool = False
    drives: list[DriveMapping] = field(default_factory=list)
    # Chaque port série/parallèle doit être déclaré explicitement — pas de
    # notion de "tout rediriger" pour ces deux-là (comme sous Windows).
    serial_ports: list[SerialMapping] = field(default_factory=list)
    parallel_ports: list[ParallelMapping] = field(default_factory=list)
    # Chaînes façon xfreerdp /usb:... — voir asyncrdp_add_usb_device côté
    # shim ; l'énumération des périphériques (via libusb côté UI) et la
    # construction de ces chaînes restent à la charge de l'appelant.
    usb_devices: list[str] = field(default_factory=list)
    audio_playback: bool = True
    audio_capture: bool = False

    # --- Multi-écran ---
    # Si non vide, remplace width/height : un moniteur virtuel par entrée,
    # comme /monitors: en CLI xfreerdp. use_multimon est activé
    # automatiquement dès que `monitors` est renseigné.
    monitors: list[MonitorDef] = field(default_factory=list)

    # --- Expérience (profil de performance façon mstsc) ---
    # 1=modem, 2=broadband bas débit, 3=broadband haut débit, 6=LAN,
    # 7=détection automatique — valeurs CONNECTION_TYPE à revérifier
    # contre freerdp/settings.h de la version installée.
    connection_type: int = 6
    font_smoothing: bool = True
    desktop_composition: bool = True
    wallpaper: bool = False
    menu_animations: bool = False
    full_window_drag: bool = False
    themes: bool = True

    # --- RD Gateway (passerelle d'entreprise) ---
    gateway: GatewayOptions | None = None

    # --- RemoteApp / RAIL ---
    # Lance une application unique plutôt que le bureau complet. Limitation
    # assumée (voir asyncrdp_set_remoteapp côté shim) : pas de vraie gestion
    # multi-fenêtres, seulement une appli occupant tout l'espace client.
    remoteapp: RemoteAppOptions | None = None

    # --- Disposition clavier ---
    # Identifiant KLID Windows (ex: 0x0409 = anglais US, 0x040c = français).
    # None = comportement par défaut du serveur (pas de réglage envoyé).
    keyboard_layout: int | None = None

    # --- Reconnexion automatique ---
    auto_reconnect: bool = False

    # --- Fuseau horaire ---
    # Envoie automatiquement le fuseau du système local à la connexion —
    # pas de champ à renseigner, contrairement au reste de RdpOptions.
    send_client_timezone: bool = True

    # --- Cookie de redirection / load balancing ---
    # Rarement nécessaire manuellement : le client le reçoit normalement du
    # serveur lors d'une redirection automatique vers un nœud de ferme RDS.
    load_balance_info: bytes | None = None

    # --- Pipeline graphique moderne (RDPGFX) / codecs H.264 ---
    # Câblé via gdi_graphics_pipeline_init() : les frames décodées par ce
    # chemin arrivent dans le MÊME gdi->primary_buffer que le GDI logiciel
    # classique, donc get_frame()/EndPaint fonctionnent sans changement une
    # fois le canal RDPGFX connecté. NON CONFIRMÉ frame-par-frame contre un
    # vrai flux H.264 : aucun serveur de test disponible ne négocie ce
    # canal (xrdp ne le supporte pas — voir CLAUDE.md). À activer d'abord
    # contre un serveur qui le supporte réellement (Windows RDS, ou un
    # xfreerdp-shadow avec /gfx) avant de considérer ce chemin comme fiable.
    enable_graphics_pipeline: bool = False
    enable_h264: bool = False
    enable_h264_444: bool = False
    enable_progressive_codec: bool = False

    @traced
    def _apply(self, context) -> bool:
        """Applique tous les réglages au contexte FreeRDP. Retourne False
        si un des appels a échoué (settings non appliqués correctement)."""
        ok = True
        if self.monitors:
            # En multi-écran, DesktopWidth/Height doit couvrir l'union des
            # moniteurs (bounding box), pas juste le premier écran.
            total_w = max(m.x + m.width for m in self.monitors)
            total_h = max(m.y + m.height for m in self.monitors)
            ok &= shim.asyncrdp_set_resolution(context, total_w, total_h)
        else:
            ok &= shim.asyncrdp_set_resolution(context, self.width, self.height)
        ok &= shim.asyncrdp_set_color_depth(context, self.color_depth)
        ok &= shim.asyncrdp_set_security_nla(context, self.enable_nla, self.enable_tls)
        ok &= shim.asyncrdp_set_ignore_certificate(context, self.ignore_certificate)

        ok &= shim.asyncrdp_set_redirect_clipboard(context, self.redirect_clipboard)
        ok &= shim.asyncrdp_set_redirect_printers(context, self.redirect_printers)
        for printer in self.printers:
            driver = printer.driver.encode() if printer.driver else ffi.NULL
            if not shim.asyncrdp_add_printer(context, printer.name.encode(), driver, printer.is_default):
                logger.warning("Échec d'ajout de l'imprimante {!r}", printer.name)
                ok = False
        ok &= shim.asyncrdp_set_redirect_smartcards(context, self.redirect_smartcards)
        ok &= shim.asyncrdp_set_redirect_drives(context, self.redirect_drives or bool(self.drives))
        for drive in self.drives:
            if not shim.asyncrdp_add_drive(context, drive.name.encode(), drive.path.encode()):
                logger.warning("Échec d'ajout du lecteur {!r} -> {!r}", drive.name, drive.path)
                ok = False
        for serial in self.serial_ports:
            if not shim.asyncrdp_add_serial_port(context, serial.name.encode(), serial.path.encode()):
                logger.warning("Échec d'ajout du port série {!r} -> {!r}", serial.name, serial.path)
                ok = False
        for parallel in self.parallel_ports:
            if not shim.asyncrdp_add_parallel_port(context, parallel.name.encode(), parallel.path.encode()):
                logger.warning("Échec d'ajout du port parallèle {!r} -> {!r}", parallel.name, parallel.path)
                ok = False
        for usb_args in self.usb_devices:
            if not shim.asyncrdp_add_usb_device(context, usb_args.encode()):
                logger.warning("Échec d'ajout du périphérique USB {!r}", usb_args)
                ok = False
        ok &= shim.asyncrdp_set_audio_playback(context, self.audio_playback)
        ok &= shim.asyncrdp_set_audio_capture(context, self.audio_capture)

        if self.monitors:
            xs = ffi.new("int[]", [m.x for m in self.monitors])
            ys = ffi.new("int[]", [m.y for m in self.monitors])
            ws = ffi.new("int[]", [m.width for m in self.monitors])
            hs = ffi.new("int[]", [m.height for m in self.monitors])
            prim = ffi.new("int[]", [int(m.is_primary) for m in self.monitors])
            ok &= shim.asyncrdp_set_monitors(context, xs, ys, ws, hs, prim, len(self.monitors))
            ok &= shim.asyncrdp_set_use_multimon(context, 1)
            logger.debug("Multi-écran : {} moniteur(s) déclarés", len(self.monitors))

        ok &= shim.asyncrdp_set_connection_type(context, self.connection_type)
        ok &= shim.asyncrdp_set_font_smoothing(context, self.font_smoothing)
        ok &= shim.asyncrdp_set_desktop_composition(context, self.desktop_composition)
        ok &= shim.asyncrdp_set_wallpaper(context, self.wallpaper)
        ok &= shim.asyncrdp_set_menu_animations(context, self.menu_animations)
        ok &= shim.asyncrdp_set_full_window_drag(context, self.full_window_drag)
        ok &= shim.asyncrdp_set_themes(context, self.themes)

        if self.gateway:
            gw = self.gateway
            ok &= shim.asyncrdp_set_gateway(
                context,
                gw.hostname.encode(),
                gw.port,
                gw.username.encode() if gw.username else ffi.NULL,
                gw.password.encode() if gw.password else ffi.NULL,
                gw.domain.encode() if gw.domain else ffi.NULL,
                gw.usage_method,
            )
            logger.debug("RD Gateway configurée : {}:{}", gw.hostname, gw.port)

        if self.remoteapp:
            ra = self.remoteapp
            ok &= shim.asyncrdp_set_remoteapp(
                context,
                ra.program.encode(),
                ra.name.encode() if ra.name else ffi.NULL,
                ra.cmdline.encode() if ra.cmdline else ffi.NULL,
            )
            logger.debug("RemoteApp configurée : {}", ra.program)

        if self.keyboard_layout is not None:
            ok &= shim.asyncrdp_set_keyboard_layout(context, self.keyboard_layout)

        ok &= shim.asyncrdp_set_auto_reconnect(context, self.auto_reconnect)

        if self.send_client_timezone and not shim.asyncrdp_set_client_timezone(context):
            logger.warning("Échec de récupération/envoi du fuseau horaire local, ignoré")

        if self.load_balance_info:
            buf = ffi.new("unsigned char[]", self.load_balance_info)
            ok &= shim.asyncrdp_set_load_balance_info(context, buf, len(self.load_balance_info))

        if self.enable_graphics_pipeline:
            logger.debug(
                "enable_graphics_pipeline=True : le canal RDPGFX sera câblé dans le GDI "
                "logiciel via gdi_graphics_pipeline_init() dès qu'il se connectera — voir "
                "Client._gfx / asyncrdp_on_gfx_ready(). Non confirmé frame-par-frame contre "
                "un vrai flux H.264 (aucun serveur de test disponible ne négocie ce canal ; "
                "voir CLAUDE.md pour le détail)."
            )
            ok &= shim.asyncrdp_set_graphics_pipeline(
                context,
                self.enable_graphics_pipeline,
                self.enable_h264,
                self.enable_h264_444,
                self.enable_progressive_codec,
            )

        logger.debug(
            "RdpOptions appliquées : {}x{}@{}bpp, clipboard={} imprimantes={}(+{} nommées) "
            "smartcards={} disques={} série={} parallèle={} usb={} audio(play={},cap={}) "
            "moniteurs={} type_connexion={}",
            self.width,
            self.height,
            self.color_depth,
            self.redirect_clipboard,
            self.redirect_printers,
            len(self.printers),
            self.redirect_smartcards,
            [d.name for d in self.drives],
            len(self.serial_ports),
            len(self.parallel_ports),
            len(self.usb_devices),
            self.audio_playback,
            self.audio_capture,
            len(self.monitors),
            self.connection_type,
        )
        return bool(ok)


class Keyboard:
    @traced
    def __init__(self, context):
        self._context = context

    @traced
    def write(self, text: str) -> None:
        """Envoie chaque caractère comme événement clavier unicode
        (down puis up). Convient pour du texte ; pour les touches de
        contrôle (Entrée, Tab, flèches...) utiliser key_press()."""
        logger.debug("Keyboard.write() : {!r} ({} caractères)", text, len(text))
        for char in text:
            code = ord(char)
            shim.asyncrdp_send_unicode_key(self._context, code, 1)
            shim.asyncrdp_send_unicode_key(self._context, code, 0)

    @traced
    def key_press(self, key_name: str) -> None:
        """Touche nommée (voir asyncrdp_scancode_from_name côté shim pour
        la liste couverte : return, tab, backspace, escape, flèches,
        f1-f12, etc.)."""
        scancode = shim.asyncrdp_scancode_from_name(key_name.encode())
        if scancode == 0:
            logger.warning("Keyboard.key_press() : touche inconnue {!r}", key_name)
            raise ValueError(f"touche inconnue : {key_name!r}")
        logger.debug("Keyboard.key_press() : {!r} (scancode={})", key_name, scancode)
        shim.asyncrdp_send_scancode_key(self._context, scancode, 1)
        shim.asyncrdp_send_scancode_key(self._context, scancode, 0)


class Mouse:
    @traced
    def __init__(self, context):
        self._context = context
        self._x = 0
        self._y = 0

    @traced
    def move(self, x: int, y: int) -> None:
        logger.trace("Mouse.move() : {}, {}", x, y)
        self._x, self._y = x, y
        shim.asyncrdp_send_mouse_move(self._context, x, y)

    @traced
    def button_press(self, button: str = "left") -> None:
        flag = shim.asyncrdp_ptr_flag_button(button.encode())
        if flag == 0:
            logger.warning("Mouse.button_press() : bouton inconnu {!r}", button)
            raise ValueError(f"bouton inconnu : {button!r}")
        logger.trace("Mouse.button_press() : {!r} à ({}, {})", button, self._x, self._y)
        shim.asyncrdp_send_mouse_button(self._context, self._x, self._y, flag, 1)

    @traced
    def button_release(self, button: str = "left") -> None:
        flag = shim.asyncrdp_ptr_flag_button(button.encode())
        if flag == 0:
            logger.warning("Mouse.button_release() : bouton inconnu {!r}", button)
            raise ValueError(f"bouton inconnu : {button!r}")
        logger.trace("Mouse.button_release() : {!r} à ({}, {})", button, self._x, self._y)
        shim.asyncrdp_send_mouse_button(self._context, self._x, self._y, flag, 0)

    @traced
    def click(self, button: str = "left") -> None:
        """Convenience : press + release immédiat. Pour un drag (glisser-
        déposer, sélection), utiliser button_press()/move()/button_release()
        séparément."""
        logger.debug("Mouse.click() : {!r} à ({}, {})", button, self._x, self._y)
        self.button_press(button)
        self.button_release(button)

    @traced
    def scroll(self, delta: int) -> None:
        """delta positif = molette vers le haut, négatif = vers le bas."""
        logger.trace("Mouse.scroll() : delta={}", delta)
        shim.asyncrdp_send_mouse_wheel(self._context, self._x, self._y, delta)


@traced
def _raise_last_error(context) -> None:
    code = shim.asyncrdp_get_last_error(context)
    msg_ptr = shim.asyncrdp_get_last_error_string(code)
    msg = ffi.string(msg_ptr).decode() if msg_ptr != ffi.NULL else "erreur inconnue"
    logger.error("Erreur FreeRDP [{}] {}", code, msg)
    raise FreeRDPError(f"[{code}] {msg}")


# Le callback EndPaint (extern "Python") ne reçoit qu'un rdpContext* — pas
# moyen d'y accrocher un objet Python directement sans dépendre du layout
# interne de rdpContext. On passe donc par des tables globales indexées
# sur l'adresse du contexte, peuplées dans connect() et nettoyées dans
# disconnect().
_clients_by_context: dict[int, Client] = {}


@traced
def _ctx_key(ctx) -> int:
    return int(ffi.cast("uintptr_t", ctx))


@ffi.def_extern()
@traced
def asyncrdp_on_end_paint(ctx) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is not None:
        client._on_end_paint()
    else:
        logger.warning("EndPaint reçu pour un contexte inconnu : {:#x}", _ctx_key(ctx))
    return 1  # TRUE côté C


@ffi.def_extern()
@traced
def asyncrdp_on_disp_ready(ctx, disp) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("Canal disp prêt pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    logger.debug("Canal Display Control (resize) prêt")
    client._disp = disp
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_gfx_ready(ctx, gfx) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("Canal RDPGFX prêt pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    logger.debug("Canal RDPGFX (pipeline graphique moderne) prêt")
    client._gfx = gfx
    # Le canal RDPGFX peut se connecter AVANT ou APRÈS notre appel à
    # gdi_init() (fait après asyncrdp_connect() côté Python, alors que ce
    # callback peut être déclenché pendant la négociation elle-même,
    # potentiellement depuis le thread executor). Si gdi_init a déjà eu
    # lieu (_gdi_ready déjà True), on câble tout de suite ; sinon c'est
    # connect() qui s'en chargera une fois gdi_init fait.
    if client._gdi_ready and client._options_wants_gfx and not shim.asyncrdp_init_graphics_pipeline(ctx, gfx):
        logger.warning("Échec de l'initialisation tardive du pipeline graphique RDPGFX")
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_cliprdr_ready(ctx, cliprdr) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("Canal cliprdr prêt pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    logger.debug("Canal cliprdr prêt")
    # Renseigné dès la connexion du canal, pas seulement de façon réactive
    # via ServerFormatList/DataRequest/DataResponse — sinon announce_local_*
    # / request_remote_* restent bloqués indéfiniment si le serveur attend
    # passivement que le client parle en premier (observé en pratique
    # contre un vrai serveur xrdp).
    client.clipboard._cliprdr = cliprdr
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_clipboard_format_list(ctx, cliprdr, format_ids, format_names, count) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("ServerFormatList reçu pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    # format_names[i] est un char* C, potentiellement NULL (formats standard
    # CF_*) — à copier immédiatement en str Python, la mémoire C ne survit
    # pas après ce callback.
    formats: list[tuple[int, str | None]] = []
    for i in range(count):
        name_ptr = format_names[i]
        name = ffi.string(name_ptr).decode("utf-8", errors="replace") if name_ptr != ffi.NULL else None
        formats.append((format_ids[i], name))
    logger.debug("Clipboard: ServerFormatList reçu, formats={}", formats)
    client._loop.call_soon_threadsafe(client.clipboard._on_server_format_list, cliprdr, formats)
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_clipboard_data_request(ctx, cliprdr, format_id) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("ServerFormatDataRequest reçu pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    logger.debug("Clipboard: ServerFormatDataRequest reçu, format_id={}", format_id)
    client._loop.call_soon_threadsafe(client.clipboard._on_server_data_request, cliprdr, format_id)
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_clipboard_data_response(ctx, cliprdr, data, data_len) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("ServerFormatDataResponse reçu pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    raw = bytes(ffi.buffer(data, data_len))
    logger.debug("Clipboard: ServerFormatDataResponse reçu, {} octets", data_len)
    client._loop.call_soon_threadsafe(client.clipboard._on_server_data_response, raw)
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_clipboard_file_contents_request(
    ctx, cliprdr, stream_id, list_index, flags, position_low, position_high, requested_size
) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("ServerFileContentsRequest reçu pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    logger.debug("Clipboard: ServerFileContentsRequest stream={} index={} flags={}", stream_id, list_index, flags)
    client._loop.call_soon_threadsafe(
        client.clipboard._on_server_file_contents_request,
        cliprdr,
        stream_id,
        list_index,
        flags,
        position_low,
        position_high,
        requested_size,
    )
    return 1


@ffi.def_extern()
@traced
def asyncrdp_on_clipboard_file_contents_response(ctx, cliprdr, stream_id, data, data_len) -> int:
    client = _clients_by_context.get(_ctx_key(ctx))
    if client is None:
        logger.warning("ServerFileContentsResponse reçu pour un contexte inconnu : {:#x}", _ctx_key(ctx))
        return 1
    raw = bytes(ffi.buffer(data, data_len)) if data != ffi.NULL and data_len else b""
    logger.debug("Clipboard: ServerFileContentsResponse stream={}, {} octets", stream_id, len(raw))
    client._loop.call_soon_threadsafe(client.clipboard._on_server_file_contents_response, stream_id, raw)
    return 1


# Formats CLIPRDR standard (MS-RDPECLIP §2.2.3.1.1 pour le texte ;
# CF_DIB/CF_DIBV5 sont les constantes Win32 GDI historiques, réutilisées
# telles quelles par CLIPRDR pour les images bitmap).
CF_TEXT = 1
CF_DIB = 8
CF_UNICODETEXT = 13
CF_DIBV5 = 17

# Nom de format enregistré (pas une constante numérique fixe — l'ID réel
# est négocié par session, cf. FileDescriptor.formatName dans
# ServerFormatList). "FileContents" est le format compagnon utilisé pour
# le protocole de transfert (mais on passe par les PDU FileContentsRequest/
# Response dédiées, pas par un data_request/response classique sur ce nom).
CF_FILE_GROUP_DESCRIPTOR_W = "FileGroupDescriptorW"

_FILEDESCRIPTOR_SIZE = 592  # MS-RDPECLIP §2.2.5.2.3.1, structure FILEDESCRIPTORW
_FILE_ATTRIBUTE_DIRECTORY = 0x10

FILECONTENTS_SIZE = 0x1
FILECONTENTS_RANGE = 0x2

# Taille de bloc utilisée pour les requêtes FILECONTENTS_RANGE successives
# lors du téléchargement d'un fichier distant — une valeur prudente, à
# ajuster si le serveur supporte des blocs plus gros (pas de négociation
# de MTU exposée ici, contrairement au VC générique).
_FILE_CHUNK_SIZE = 512 * 1024


@dataclass
class RemoteFileInfo:
    """Une entrée du dernier FileGroupDescriptorW reçu du serveur —
    métadonnées seules, pas le contenu (voir Client.clipboard pour le
    téléchargement effectif)."""

    index: int
    name: str
    size: int
    is_directory: bool


@traced
def _parse_file_group_descriptor(raw: bytes) -> list[RemoteFileInfo]:
    """Décode le format enregistré FileGroupDescriptorW : un compteur
    32 bits suivi d'un tableau de structures FILEDESCRIPTORW (592 octets
    chacune, cf. MS-RDPECLIP §2.2.5.2.3 et Win32 FILEDESCRIPTORW)."""
    if len(raw) < 4:
        return []
    (count,) = struct.unpack_from("<I", raw, 0)
    items: list[RemoteFileInfo] = []
    offset = 4
    for i in range(count):
        chunk = raw[offset : offset + _FILEDESCRIPTOR_SIZE]
        if len(chunk) < _FILEDESCRIPTOR_SIZE:
            logger.warning("FileGroupDescriptorW tronqué à l'entrée {}", i)
            break
        # Layout : dwFlags(4) clsid(16) sizel(8) pointl(8) dwFileAttributes(4)
        #          ftCreationTime(8) ftLastAccessTime(8) ftLastWriteTime(8)
        #          nFileSizeHigh(4) nFileSizeLow(4) cFileName(520, WCHAR[260])
        (dw_file_attributes,) = struct.unpack_from("<I", chunk, 4 + 16 + 8 + 8)
        size_high, size_low = struct.unpack_from("<II", chunk, 4 + 16 + 8 + 8 + 4 + 24)
        name_offset = 4 + 16 + 8 + 8 + 4 + 24 + 4 + 4
        name_raw = chunk[name_offset : name_offset + 520]
        name = name_raw.decode("utf-16-le", errors="replace").split("\x00", 1)[0]
        items.append(
            RemoteFileInfo(
                index=i,
                name=name,
                size=(size_high << 32) | size_low,
                is_directory=bool(dw_file_attributes & _FILE_ATTRIBUTE_DIRECTORY),
            )
        )
        offset += _FILEDESCRIPTOR_SIZE
    return items


@traced
def _walk_paths_for_descriptor(paths: list[str]) -> list[tuple[str | None, str]]:
    """
    Parcourt récursivement les dossiers sélectionnés et renvoie une liste
    de (chemin_absolu_ou_None, nom_relatif). Le nom relatif utilise '\\'
    comme séparateur (convention Windows attendue dans cFileName pour
    représenter l'arborescence en une seule liste, cf. MS-RDPECLIP), avec
    pour racine le nom du fichier/dossier tel que sélectionné (pas son
    chemin parent local complet).

    chemin_absolu vaut None pour les entrées "dossier" : elles n'ont pas
    de contenu à transférer via FileContentsRequest, juste une structure
    à recréer côté récepteur.
    """
    entries: list[tuple[str | None, str]] = []
    for path in paths:
        base_name = os.path.basename(path.rstrip(os.sep))
        if os.path.isdir(path):
            entries.append((None, base_name))
            for root, dirs, files in os.walk(path):
                rel_root = os.path.relpath(root, path)
                for d in dirs:
                    rel = d if rel_root == "." else f"{rel_root}{os.sep}{d}"
                    entries.append((None, f"{base_name}\\{rel}".replace(os.sep, "\\")))
                for f in files:
                    rel = f if rel_root == "." else f"{rel_root}{os.sep}{f}"
                    abs_path = os.path.join(root, f)
                    entries.append((abs_path, f"{base_name}\\{rel}".replace(os.sep, "\\")))
        else:
            entries.append((path, base_name))
    return entries


@traced
def _pack_file_group_descriptor(paths: list[str]) -> tuple[bytes, list[str | None]]:
    """
    Construit un FileGroupDescriptorW à partir de chemins locaux (dossiers
    inclus, parcourus récursivement), pour l'annonce client -> serveur
    (announce_local_files()). Renvoie aussi la liste des chemins absolus
    dans le même ordre que les entrées du descriptor (None pour les
    dossiers) — à conserver telle quelle pour répondre correctement aux
    ServerFileContentsRequest ultérieurs, qui référencent un fichier par
    son index dans CETTE liste, pas par son chemin.
    """
    entries = _walk_paths_for_descriptor(paths)
    out = struct.pack("<I", len(entries))
    abs_paths: list[str | None] = []

    for abs_path, rel_name in entries:
        is_dir = abs_path is None
        st = os.stat(abs_path) if abs_path else None
        name_bytes = rel_name.encode("utf-16-le")[:518]  # 259 WCHAR + NUL, tronqué si besoin
        name_bytes = name_bytes + b"\x00" * (520 - len(name_bytes))

        dw_flags = 0
        clsid = b"\x00" * 16
        sizel = b"\x00" * 8
        pointl = b"\x00" * 8
        dw_file_attributes = _FILE_ATTRIBUTE_DIRECTORY if is_dir else 0
        filetimes = b"\x00" * 24  # cf. limitation ci-dessous
        size = 0 if is_dir else st.st_size
        size_high = (size >> 32) & 0xFFFFFFFF
        size_low = size & 0xFFFFFFFF

        out += struct.pack("<I", dw_flags)
        out += clsid
        out += sizel
        out += pointl
        out += struct.pack("<I", dw_file_attributes)
        out += filetimes
        out += struct.pack("<II", size_high, size_low)
        out += name_bytes
        abs_paths.append(abs_path)

    return out, abs_paths


class Clipboard:
    """
    Synchronisation presse-papier bidirectionnelle avec le serveur RDP :
    texte (CF_UNICODETEXT), image (CF_DIB) et listes de fichiers
    (FileGroupDescriptorW + transfert de contenu via File Contents
    Request/Response, MS-RDPECLIP §2.2.5).

    Câblage attendu côté GCM/GTK4 (voir gcm_gtk4_clipboard_bridge.py) :
      - presse-papier LOCAL change -> announce_local_text() /
        announce_local_image() / announce_local_files()
      - le serveur annonce du texte/une image -> on_remote_text_changed /
        on_remote_image_changed (callbacks fournis par l'appelant)
      - le serveur annonce des fichiers -> on_remote_files_changed reçoit
        la LISTE des métadonnées (RemoteFileInfo) ; le contenu de chaque
        fichier n'est téléchargé qu'à la demande via
        request_remote_file_contents(index) — pas automatiquement pour
        tous les fichiers annoncés (peut être volumineux).
      - le serveur DEMANDE le contenu local -> get_local_text /
        get_local_image_dib / get_local_files (callbacks fournis par
        l'appelant)
    """

    @traced
    def __init__(self, context, loop: asyncio.AbstractEventLoop):
        self._context = context
        self._loop = loop
        self._cliprdr = None  # void* CliprdrClientContext*, reçu au 1er event
        self._pending_data_request: asyncio.Future[bytes] | None = None
        self._pending_data_format: int | None = None
        self._format_ids: dict[str, int] = {}  # nom de format enregistré -> id de session

        self._pending_file_requests: dict[int, asyncio.Future[bytes]] = {}
        self._next_stream_id = 1
        self._local_files: list[str | None] = []  # chemins absolus alignés sur le descriptor envoyé (None = dossier)
        self._pending_local_selection: list[str] = []  # sélection top-level mémorisée par announce_local_files()
        self._remote_files: list[RemoteFileInfo] = []
        # Références aux tâches "fire-and-forget" créées par _spawn() ci-dessous :
        # sans ça, l'event loop ne garde qu'une référence faible et peut
        # garbage-collecter une tâche en plein milieu de son exécution.
        self._background_tasks: set[asyncio.Task] = set()

        # Callbacks fournis par l'intégration GTK4 (voir bridge) :
        self.on_remote_text_changed = None  # Callable[[str], None]
        self.on_remote_image_changed = None  # Callable[[bytes], None]  (DIB brut)
        self.on_remote_files_changed = None  # Callable[[list[RemoteFileInfo]], None]
        self.get_local_text = None  # Callable[[], str]
        self.get_local_image_dib = None  # Callable[[], bytes | None]
        self.get_local_files = None  # Callable[[], list[str]]  (chemins)

    @traced
    def _spawn(self, coro) -> None:
        """Lance `coro` en tâche de fond en gardant une référence forte
        (voir `self._background_tasks` dans `__init__`) jusqu'à sa fin."""
        task = asyncio.ensure_future(coro, loop=self._loop)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Réception : le serveur annonce un nouveau contenu
    # ------------------------------------------------------------------

    @traced
    def _on_server_format_list(self, cliprdr, formats: list[tuple[int, str | None]]) -> None:
        self._cliprdr = cliprdr
        ids_by_name = {name: fid for fid, name in formats if name}
        self._format_ids.update(ids_by_name)
        plain_ids = [fid for fid, _ in formats]

        if CF_FILE_GROUP_DESCRIPTOR_W in ids_by_name:
            logger.info("Clipboard: liste de fichiers disponible côté serveur RDP")
            self._spawn(self._pull_remote_files())
        elif CF_DIB in plain_ids or CF_DIBV5 in plain_ids:
            logger.info("Clipboard: image disponible côté serveur RDP")
            self._spawn(self._pull_remote_image())
        elif CF_UNICODETEXT in plain_ids or CF_TEXT in plain_ids:
            logger.info("Clipboard: contenu texte disponible côté serveur RDP")
            self._spawn(self._pull_remote_text())
        else:
            logger.debug("Clipboard: aucun format supporté annoncé par le serveur, ignoré")

    @traced
    async def _pull_remote_text(self) -> None:
        text = await self.request_remote_text()
        if self.on_remote_text_changed is not None:
            self.on_remote_text_changed(text)

    @traced
    async def _pull_remote_image(self) -> None:
        dib = await self.request_remote_image()
        if dib and self.on_remote_image_changed is not None:
            self.on_remote_image_changed(dib)

    @traced
    async def _pull_remote_files(self) -> None:
        files = await self.request_remote_file_list()
        self._remote_files = files
        if self.on_remote_files_changed is not None:
            self.on_remote_files_changed(files)

    @traced
    def _on_server_data_request(self, cliprdr, format_id: int) -> None:
        self._cliprdr = cliprdr

        if format_id == self._format_ids.get(CF_FILE_GROUP_DESCRIPTOR_W):
            paths = self.get_local_files() if self.get_local_files is not None else self._pending_local_selection
            data, abs_paths = _pack_file_group_descriptor(paths)
            self._local_files = abs_paths
            logger.debug("Clipboard: envoi de la liste de {} entrée(s) au serveur (dossiers inclus)", len(abs_paths))
            buf = ffi.new("unsigned char[]", data)
            shim.asyncrdp_clipboard_send_data_response(cliprdr, buf, len(data))
            return

        if format_id in (CF_DIB, CF_DIBV5):
            dib = self.get_local_image_dib() if self.get_local_image_dib is not None else None
            if not dib:
                shim.asyncrdp_clipboard_send_data_response(cliprdr, ffi.NULL, 0)
                return
            logger.debug("Clipboard: envoi d'une image de {} octets au serveur", len(dib))
            buf = ffi.new("unsigned char[]", dib)
            shim.asyncrdp_clipboard_send_data_response(cliprdr, buf, len(dib))
            return

        if format_id in (CF_TEXT, CF_UNICODETEXT):
            text = self.get_local_text() if self.get_local_text is not None else ""
            logger.debug("Clipboard: envoi de {} caractères au serveur", len(text))
            encoded = (text + "\x00").encode("utf-16-le")
            buf = ffi.new("unsigned char[]", encoded)
            shim.asyncrdp_clipboard_send_data_response(cliprdr, buf, len(encoded))
            return

        logger.debug("Clipboard: format {} demandé, non supporté", format_id)
        shim.asyncrdp_clipboard_send_data_response(cliprdr, ffi.NULL, 0)

    @traced
    def _on_server_data_response(self, raw: bytes) -> None:
        if self._pending_data_request is not None and not self._pending_data_request.done():
            self._pending_data_request.set_result(raw)

    @traced
    def _on_server_file_contents_request(
        self,
        cliprdr,
        stream_id: int,
        list_index: int,
        flags: int,
        position_low: int,
        position_high: int,
        requested_size: int,
    ) -> None:
        """Le serveur demande le contenu d'un fichier que LE CLIENT a
        annoncé (push local -> serveur)."""
        self._cliprdr = cliprdr
        if list_index >= len(self._local_files):
            logger.warning("Clipboard: FileContentsRequest sur un index inconnu ({})", list_index)
            shim.asyncrdp_clipboard_send_file_contents_response(cliprdr, stream_id, ffi.NULL, 0)
            return

        path = self._local_files[list_index]
        if path is None:
            logger.debug(
                "Clipboard: FileContentsRequest sur une entrée dossier (index {}), aucun contenu à transférer",
                list_index,
            )
            shim.asyncrdp_clipboard_send_file_contents_response(cliprdr, stream_id, ffi.NULL, 0)
            return
        try:
            if flags & FILECONTENTS_SIZE:
                size = os.path.getsize(path)
                payload = struct.pack("<Q", size)
            else:
                offset = (position_high << 32) | position_low
                with open(path, "rb") as f:
                    f.seek(offset)
                    payload = f.read(requested_size)
        except OSError as exc:
            logger.warning("Clipboard: lecture locale impossible pour {!r} : {}", path, exc)
            shim.asyncrdp_clipboard_send_file_contents_response(cliprdr, stream_id, ffi.NULL, 0)
            return

        logger.debug(
            "Clipboard: envoi de {} octets pour le fichier local {!r} (stream={})", len(payload), path, stream_id
        )
        buf = ffi.new("unsigned char[]", payload)
        shim.asyncrdp_clipboard_send_file_contents_response(cliprdr, stream_id, buf, len(payload))

    @traced
    def _on_server_file_contents_response(self, stream_id: int, raw: bytes) -> None:
        future = self._pending_file_requests.pop(stream_id, None)
        if future is not None and not future.done():
            future.set_result(raw)

    # ------------------------------------------------------------------
    # Demandes explicites : client -> serveur
    # ------------------------------------------------------------------

    @traced
    async def _request_data(self, format_id: int) -> bytes:
        if self._cliprdr is None:
            logger.debug("Clipboard: pas encore de canal cliprdr établi")
            return b""
        self._pending_data_request = self._loop.create_future()
        self._pending_data_format = format_id
        shim.asyncrdp_clipboard_request_data(self._cliprdr, format_id)
        try:
            return await asyncio.wait_for(self._pending_data_request, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Clipboard: timeout en attendant la réponse du serveur (format {})", format_id)
            return b""

    @traced
    async def request_remote_text(self) -> str:
        """Demande explicitement le texte actuel du presse-papier distant."""
        raw = await self._request_data(CF_UNICODETEXT)
        if not raw:
            return ""
        try:
            return raw.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            logger.warning("Clipboard: contenu texte non décodable en UTF-16LE, ignoré")
            return ""

    @traced
    async def request_remote_image(self) -> bytes:
        """Demande l'image actuelle du presse-papier distant. Retourne un
        DIB brut (BITMAPINFOHEADER + pixels, format Win32 historique) — la
        conversion en PNG/texture GTK4 est à la charge de l'appelant."""
        return await self._request_data(CF_DIB)

    @traced
    async def request_remote_file_list(self) -> list[RemoteFileInfo]:
        """Demande la liste (métadonnées seules) des fichiers actuellement
        annoncés par le serveur. Le contenu de chaque fichier doit être
        téléchargé séparément via request_remote_file_contents()."""
        format_id = self._format_ids.get(CF_FILE_GROUP_DESCRIPTOR_W)
        if format_id is None:
            logger.debug("Clipboard: pas de format FileGroupDescriptorW connu pour l'instant")
            return []
        raw = await self._request_data(format_id)
        files = _parse_file_group_descriptor(raw)
        self._remote_files = files
        return files

    @traced
    async def request_remote_file_contents(self, index: int) -> bytes:
        """
        Télécharge le contenu complet du fichier distant à l'index donné
        (voir request_remote_file_list() pour obtenir les index/tailles).
        Boucle en interne sur des requêtes FILECONTENTS_RANGE successives
        par blocs de _FILE_CHUNK_SIZE — pas de reprise sur erreur/coupure
        dans cette première passe (à ajouter si des fichiers volumineux
        posent problème en pratique).
        """
        if self._cliprdr is None:
            logger.debug("Clipboard: pas encore de canal cliprdr établi")
            return b""
        if index >= len(self._remote_files):
            logger.warning("Clipboard: index de fichier distant inconnu ({})", index)
            return b""

        info = self._remote_files[index]
        chunks: list[bytes] = []
        offset = 0
        logger.debug("Clipboard: téléchargement de {!r} ({} octets)", info.name, info.size)

        while offset < info.size:
            size_to_request = min(_FILE_CHUNK_SIZE, info.size - offset)
            stream_id = self._next_stream_id
            self._next_stream_id += 1

            future = self._loop.create_future()
            self._pending_file_requests[stream_id] = future
            shim.asyncrdp_clipboard_request_file_contents(
                self._cliprdr,
                stream_id,
                index,
                FILECONTENTS_RANGE,
                offset & 0xFFFFFFFF,
                (offset >> 32) & 0xFFFFFFFF,
                size_to_request,
            )
            try:
                chunk = await asyncio.wait_for(future, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Clipboard: timeout en téléchargeant {!r} à l'offset {}", info.name, offset)
                self._pending_file_requests.pop(stream_id, None)
                break
            if not chunk:
                logger.warning("Clipboard: bloc vide reçu pour {!r} à l'offset {}, arrêt", info.name, offset)
                break
            chunks.append(chunk)
            offset += len(chunk)

        result = b"".join(chunks)
        logger.debug("Clipboard: {!r} téléchargé ({} octets sur {} attendus)", info.name, len(result), info.size)
        return result

    # ------------------------------------------------------------------
    # Annonces : local -> serveur
    # ------------------------------------------------------------------

    @traced
    def _send_format_list(self, entries: list[tuple[int, str | None]]) -> None:
        if self._cliprdr is None:
            logger.debug("Clipboard: announce ignoré, pas encore de canal cliprdr")
            return
        ids = ffi.new("unsigned int[]", [fid for fid, _ in entries])
        # Un tableau de char* C : ffi.new("char[]", ...) par nom, NULL sinon,
        # puis un tableau de pointeurs vers ces buffers (gardés en vie via
        # une liste locale tant que l'appel est en cours).
        name_bufs = [ffi.new("char[]", name.encode()) if name else ffi.NULL for _, name in entries]
        names = ffi.new("char*[]", name_bufs)
        shim.asyncrdp_clipboard_send_format_list(self._cliprdr, ids, names, len(entries))

    @traced
    def announce_local_text(self, text: str) -> None:
        """À appeler quand le presse-papier LOCAL (GTK4) change vers du
        texte, pour signaler au serveur RDP qu'un nouveau contenu est
        disponible."""
        logger.debug("Clipboard: annonce locale de {} caractères au serveur", len(text))
        self._send_format_list([(CF_UNICODETEXT, None)])

    @traced
    def announce_local_image(self) -> None:
        """À appeler quand le presse-papier LOCAL contient une nouvelle
        image. Le contenu réel (DIB) n'est envoyé que si le serveur le
        demande ensuite (via get_local_image_dib)."""
        logger.debug("Clipboard: annonce locale d'une image au serveur")
        self._send_format_list([(CF_DIB, None)])

    @traced
    def announce_local_files(self, paths: list[str]) -> None:
        """À appeler quand une sélection de fichiers/dossiers locaux (ex:
        dans le gestionnaire de fichiers GTK4) doit être proposée au
        serveur RDP. La sélection est mémorisée et utilisée par défaut au
        prochain ServerFormatDataRequest — get_local_files(), si défini,
        est prioritaire (permet à l'appelant de fournir une sélection
        dynamique plutôt que figée au moment de l'annonce)."""
        self._pending_local_selection = paths
        logger.debug("Clipboard: annonce locale de {} sélection(s) au serveur", len(paths))
        # formatId=0 : ignoré côté shim quand un nom est fourni (l'ID réel
        # est négocié/retrouvé par FreeRDP via ce nom).
        self._send_format_list([(0, CF_FILE_GROUP_DESCRIPTOR_W)])


class Client:
    """Poignée exposée à l'utilisateur, retournée par connect()."""

    _REFRESH_INTERVAL = 0.25  # secondes

    @traced
    def __init__(self, context, loop: asyncio.AbstractEventLoop):
        self._context = context
        self._loop = loop
        self.keyboard = Keyboard(context)
        self.mouse = Mouse(context)
        self.clipboard = Clipboard(context, loop)
        self._registered_fds: set[int] = set()
        self._disconnect_event = asyncio.Event()
        self.last_error: Exception | None = None  # cause si déconnexion suite à une erreur (voir _on_fd_readable)
        self._refresh_handle: asyncio.TimerHandle | None = None

        # maxsize=1 volontaire : on ne veut jamais consommer une frame
        # périmée, seulement la plus récente. Une frame en retard qui
        # s'accumule dans une queue illimitée n'a aucune valeur pour de
        # l'affichage temps réel.
        self.frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self.frame_size: tuple[int, int] | None = None  # (width, height)

        self._disp = None  # void* DispClientContext*, reçu quand le canal est prêt

        self._gfx = None  # void* RdpgfxClientContext*, reçu quand le canal est prêt
        self._gdi_ready = False  # True une fois gdi_init() fait côté connect()
        self._options_wants_gfx = False  # copie de options.enable_graphics_pipeline

    @traced
    def request_resize(self, width: int, height: int) -> bool:
        """Demande au serveur RDP de redimensionner le bureau distant (ex:
        suite à un redimensionnement de la fenêtre GCM). Retourne False si
        le canal Display Control n'est pas encore disponible (juste après
        connect(), avant la négociation post-connexion) ou si l'envoi a
        échoué — le serveur peut aussi silencieusement ignorer/clamp la
        valeur ; la confirmation se voit dans les frames suivantes."""
        if self._disp is None:
            logger.debug("request_resize({}, {}) ignoré : canal disp pas encore prêt", width, height)
            return False
        logger.debug("request_resize({}, {})", width, height)
        return bool(shim.asyncrdp_send_resize(self._disp, width, height))

    @traced
    def _on_end_paint(self) -> None:
        """Appelé depuis le callback C EndPaint — potentiellement depuis un
        thread FreeRDP interne (ex: pendant le connect() qui tourne dans
        l'executor). On ne touche donc jamais la queue directement ici,
        tout passe par call_soon_threadsafe."""
        data_ptr = ffi.new("unsigned char**")
        width = ffi.new("int*")
        height = ffi.new("int*")
        stride = ffi.new("int*")

        ok = shim.asyncrdp_get_framebuffer(self._context, data_ptr, width, height, stride)
        if not ok:
            logger.debug("EndPaint: framebuffer indisponible, frame ignorée")
            return

        w, h, s = width[0], height[0], stride[0]
        # Copie immédiate : le buffer C est réutilisé à la frame suivante.
        raw = bytes(ffi.buffer(data_ptr[0], s * h))
        logger.trace("Frame reçue : {}x{}, stride={}, {} octets", w, h, s, len(raw))

        self._loop.call_soon_threadsafe(self._push_frame, raw, (w, h))

    @traced
    def _push_frame(self, raw: bytes, size: tuple[int, int]) -> None:
        self.frame_size = size
        if self.frames.full():
            # On jette l'ancienne frame non consommée plutôt que de
            # bloquer ou d'empiler du retard.
            self.frames.get_nowait()
        self.frames.put_nowait(raw)

    @traced
    async def get_frame(self) -> bytes:
        """Attend et retourne la prochaine frame (BGRA32 brut, stride
        variable — voir frame_size pour width/height)."""
        return await self.frames.get()

    @traced
    def _on_fd_readable(self, fd: int) -> None:
        logger.trace("fd {} lisible, check_event_handles()", fd)
        ok = shim.asyncrdp_check_event_handles(self._context)
        if not ok:
            # BUG RÉEL trouvé en testant contre un vrai serveur (session
            # tuée côté serveur en cours de route) : lever une exception
            # ici ne sert à rien (personne ne l'attend, un callback de
            # loop.add_reader n'est pas awaité) — asyncio se contente de
            # logger "Exception in callback" et laisse le fd enregistré.
            # Comme le fd reste "lisible" en continu une fois la connexion
            # cassée, ce callback était rappelé en boucle infinie, spammant
            # la même erreur indéfiniment. Fix : stocker l'erreur,
            # désenregistrer les fds, ne pas lever ici.
            code = shim.asyncrdp_get_last_error(self._context)
            msg_ptr = shim.asyncrdp_get_last_error_string(code)
            msg = ffi.string(msg_ptr).decode() if msg_ptr != ffi.NULL else "erreur inconnue"
            logger.error("check_event_handles a échoué sur fd {} : [{}] {}", fd, code, msg)
            self.last_error = FreeRDPError(f"[{code}] {msg}")
            self._disconnect_event.set()
            self._unregister_fds()
            return

        if shim.asyncrdp_shall_disconnect(self._context):
            logger.info("Le serveur/la lib demande la déconnexion")
            self._disconnect_event.set()
            self._unregister_fds()
            return

        self._sync_fds()

    @traced
    def _sync_fds(self) -> None:
        buf = ffi.new("int[]", 64)
        n = shim.asyncrdp_get_handle_fds(self._context, buf, 64)
        current = {buf[i] for i in range(n)}

        added = current - self._registered_fds
        removed = self._registered_fds - current
        for fd in added:
            self._loop.add_reader(fd, self._on_fd_readable, fd)
        for fd in removed:
            self._loop.remove_reader(fd)
        if added or removed:
            logger.debug("fds synchronisés : +{} -{} (total={})", len(added), len(removed), len(current))

        self._registered_fds = current

    @traced
    def _schedule_refresh(self) -> None:
        self._sync_fds()
        if not self._disconnect_event.is_set():
            self._refresh_handle = self._loop.call_later(self._REFRESH_INTERVAL, self._schedule_refresh)

    @traced
    def _register_fds(self) -> None:
        logger.debug("Enregistrement initial des fds")
        self._schedule_refresh()

    @traced
    def _unregister_fds(self) -> None:
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
        for fd in self._registered_fds:
            self._loop.remove_reader(fd)
        logger.debug("{} fds désenregistrés", len(self._registered_fds))
        self._registered_fds.clear()

    @traced
    async def wait_disconnected(self) -> None:
        await self._disconnect_event.wait()

    @traced
    async def disconnect(self) -> None:
        logger.info("Déconnexion demandée")
        self._unregister_fds()
        if self._gfx is not None:
            shim.asyncrdp_uninit_graphics_pipeline(self._context, self._gfx)
        # Comme asyncrdp_connect(), peut bloquer brièvement (attente du PDU
        # de déconnexion côté serveur) — même traitement via l'executor.
        await self._loop.run_in_executor(None, shim.asyncrdp_disconnect, self._context)
        logger.info("Déconnecté")


@asynccontextmanager
async def connect(
    host: str,
    port: int = 3389,
    username: str | None = None,
    password: str | None = None,
    domain: str | None = None,
    options: RdpOptions | None = None,
):
    """
    Usage:
        opts = RdpOptions(redirect_drives=True, drives=[DriveMapping("home", "/home/mathilde")])
        async with asyncrdp.connect('192.168.1.10', username='u', password='p',
                                     domain='CORP', options=opts) as client:
            ...

    domain : nécessaire pour l'authentification NTLM/Kerberos classique en
    environnement Active Directory (équivalent de "CORP\\utilisateur" ou
    du champ "Domaine" de mstsc) — sans lui, l'authentification échoue sur
    la plupart des serveurs joints à un domaine.
    """
    logger.debug("connect() : {}{}@{}:{}", f"{domain}\\" if domain else "", username or "(anonyme)", host, port)
    options = options or RdpOptions()
    loop = asyncio.get_running_loop()

    context = shim.asyncrdp_context_new(ffi.NULL)
    if context == ffi.NULL:
        logger.error("Échec de création du contexte FreeRDP")
        raise FreeRDPError("échec de création du contexte FreeRDP")
    logger.debug("Contexte FreeRDP créé : {:#x}", _ctx_key(context))

    ok = shim.asyncrdp_set_hostname(context, host.encode())
    ok &= shim.asyncrdp_set_port(context, port)
    if username:
        ok &= shim.asyncrdp_set_username(context, username.encode())
    if password:
        ok &= shim.asyncrdp_set_password(context, password.encode())
    if domain:
        ok &= shim.asyncrdp_set_domain(context, domain.encode())
    ok &= options._apply(context)
    if not ok:
        logger.error("Échec de configuration des rdpSettings")
        shim.asyncrdp_context_free(context)
        logger.debug("rdpSettings non configurés : contexte libéré, échec signalé")
        raise FreeRDPError("échec de configuration des rdpSettings")

    # À appeler après TOUS les settings et avant connect() : charge les
    # plugins de canaux (rdpdr/urbdrc/rdpsnd/etc.) correspondant aux
    # redirections activées ci-dessus.
    if not shim.asyncrdp_load_addins(context):
        logger.warning("Échec du chargement de certains addins de canaux (redirections partiellement indisponibles)")

    # BUG RÉEL trouvé en testant contre un vrai serveur (xrdp) : le canal
    # cliprdr se connecte PENDANT la négociation initiale (à l'intérieur
    # même de asyncrdp_connect() / freerdp_connect() plus bas). S'abonner
    # au PubSub seulement APRÈS ce connect(), comme le faisait une version
    # précédente de ce code, rate silencieusement et définitivement
    # l'événement ChannelConnected pour tout canal qui se connecte pendant
    # la poignée de main — observé concrètement : announce_local_text()/
    # request_remote_text() restaient bloqués indéfiniment, self._cliprdr
    # ne devenant jamais autre chose que None. D'où la création du Client
    # et l'abonnement aux canaux AVANT l'appel bloquant, pas après.
    client = Client(context, loop)
    _clients_by_context[_ctx_key(context)] = client
    shim.asyncrdp_register_channels(context)
    logger.debug("Callbacks cliprdr + disp enregistrés (avant connect())")

    # asyncrdp_connect() (freerdp_connect côté C) est bloquant pendant toute
    # la négociation TLS/NLA — potentiellement plusieurs centaines de ms à
    # quelques secondes selon le réseau/serveur. On le sort du thread de la
    # loop pour ne pas geler les autres tâches asyncio pendant ce temps.
    # NOTE : les callbacks de canaux (cliprdr/disp) peuvent donc se
    # déclencher depuis CE thread executor plutôt que le thread de la loop
    # si le serveur négocie vite — les écritures qu'ils font sur le Client
    # sont de simples affectations d'attribut (protégées par le GIL), pas
    # d'accès à des structures asyncio non thread-safe, donc sans risque
    # ici, mais à garder en tête si des callbacks plus complexes s'y
    # ajoutent plus tard.
    logger.debug("Lancement de asyncrdp_connect() dans l'executor")
    connected = await loop.run_in_executor(None, shim.asyncrdp_connect, context)
    if not connected:
        logger.error("Connexion échouée vers {}:{}", host, port)
        del _clients_by_context[_ctx_key(context)]
        try:
            _raise_last_error(context)
        finally:
            shim.asyncrdp_context_free(context)
    logger.info("Connecté à {}:{}", host, port)

    if not shim.asyncrdp_gdi_init(context):
        logger.error("Échec d'initialisation du GDI logiciel")
        del _clients_by_context[_ctx_key(context)]
        shim.asyncrdp_context_free(context)
        logger.debug("GDI non initialisé : contexte libéré, échec signalé")
        raise FreeRDPError("échec d'initialisation du GDI logiciel")
    logger.debug("GDI logiciel initialisé (BGRA32)")
    client._gdi_ready = True

    if options.enable_graphics_pipeline:
        client._options_wants_gfx = True
        if client._gfx is not None:
            # Le canal RDPGFX était déjà prêt avant que gdi_init() ne le
            # soit (connecté pendant la négociation initiale) — on câble
            # maintenant. Sinon, c'est asyncrdp_on_gfx_ready() qui le fera
            # dès que le canal se connectera (cf. plus haut dans ce fichier).
            if shim.asyncrdp_init_graphics_pipeline(context, client._gfx):
                logger.debug("Pipeline graphique RDPGFX câblé dans le GDI logiciel")
            else:
                logger.warning("Échec du câblage du pipeline graphique RDPGFX")

    shim.asyncrdp_set_end_paint_callback(context)
    logger.debug("Callback EndPaint enregistré")

    client._register_fds()

    try:
        yield client
    finally:
        await client.disconnect()
        _clients_by_context.pop(_ctx_key(context), None)
        shim.asyncrdp_context_free(context)
        logger.debug("Contexte FreeRDP libéré")


# ---------------------------------------------------------------------------
# Notes d'implémentation :
#
# 1. rdpSettings : FAIT — voir asyncrdp_shim.c / build_ffi.py (mode API cffi,
#    résolution des enums FreeRDP_* par le compilateur C plutôt que devinés).
#
# 2. Fds pollables : FAIT — asyncrdp_get_handle_fds() côté shim +
#    _sync_fds()/loop.add_reader() côté Client. Refresh périodique (250ms)
#    en filet de sécurité pour les changements de jeu de handles.
#
# 3. Interop ABI/API : RÉSOLU — une seule frontière cffi (mode API, module
#    _asyncrdp_cffi). Toute nouvelle fonctionnalité doit passer par un
#    wrapper ajouté à asyncrdp_shim.c plutôt que par un dlopen séparé.
#
# 4. Callbacks GDI : FAIT — gdi_init() en GDI logiciel (décode les codecs en
#    interne, rasterise en BGRA32 plat) + callback EndPaint via
#    extern "Python", table globale {adresse contexte: Client}. Champs
#    gdi->primary_buffer/width/height/stride à revérifier contre gdi.h de
#    la version installée.
#
# 5. Threading : FAIT — asyncrdp_connect() et disconnect() passent par
#    loop.run_in_executor(None, ...). cffi (mode API) relâche le GIL pendant
#    l'appel C.
#
# 6. Clipboard : FAIT — texte (CF_UNICODETEXT), image (CF_DIB, DIB brut —
#    conversion PNG/texture à la charge de l'appelant), listes de fichiers
#    (format enregistré FileGroupDescriptorW, ID négocié par session et
#    retrouvé par nom plutôt que deviné) avec transfert de contenu complet
#    dans les deux sens via File Contents Request/Response (téléchargement
#    par blocs de _FILE_CHUNK_SIZE, upload sur demande du serveur en lisant
#    le fichier local à l'offset demandé). Canal cliprdr hooké via
#    PubSub_SubscribeChannelConnected, tous les callbacks ServerFormatList/
#    ServerFormatDataRequest/ServerFormatDataResponse/ServerFileContents*
#    relayés en Python via extern "Python".
#    Câblage GTK4 : voir gcm_gtk4_clipboard_bridge.py (texte câblé à ce
#    stade ; image/fichiers à étendre côté bridge avec la même logique).
#    Limitation assumée : FILETIME (création/accès/écriture) toujours
#    envoyés à zéro dans _pack_file_group_descriptor() — le serveur RDP
#    n'en a généralement pas l'usage pour un simple copier-coller, mais à
#    corriger si un scénario réel s'appuie dessus (ex: préserver les dates
#    lors d'un déplacement de fichiers).
#
# 6bis. Clavier/souris : FAIT — freerdp_input_send_unicode_keyboard_event
#    (texte), freerdp_input_send_keyboard_event_ex + RDP_SCANCODE_* nommés
#    (touches de contrôle), freerdp_input_send_mouse_event (déplacement,
#    clic, molette). Appels synchrones (pas de run_in_executor) : ce sont
#    des écritures bufferisées sur le canal RDP, pas une négociation
#    réseau bloquante comme connect()/disconnect() — à revoir si un usage
#    réel montre des latences inattendues.
#
# 7. Gestion d'erreurs : vérifier le nom exact du symbole
#    freerdp_get_last_error_string vs freerdp_get_last_error_name selon la
#    version de libfreerdp installée, et typer les exceptions.
#
# 8. Logging : loguru utilisé à tous les points stratégiques (connexion,
#    déconnexion, sync fds, frames, clipboard, erreurs). Configurer le sink
#    côté appelant (GCM) via logger.add(...) — ce module ne fait volontairement
#    aucune configuration de sink pour rester une bibliothèque bien élevée.
#
# 9. Affichage + input GTK4 : voir gcm_gtk4_display_bridge.py — Gtk.Picture
#    + Gdk.MemoryTexture pour les frames, EventController* pour souris/
#    clavier. Limitations notées dans ce fichier : mapping clavier partiel,
#    mise à l'échelle souris naïve sans calcul du rectangle réel sous
#    letterboxing.
#
# 10. Resize dynamique : FAIT — canal Display Control (disp, MS-RDPEDISP)
#     hooké dans le même handler PubSub que cliprdr (asyncrdp_register_channels).
#     Client.request_resize(w, h) envoie la demande ; le serveur peut
#     clamp/ignorer, la confirmation réelle vient des frames suivantes
#     (gdi->width/height déjà relus dynamiquement à chaque EndPaint).
#     Câblage GTK4 avec debounce : voir _on_widget_resize() dans
#     gcm_gtk4_display_bridge.py.
#
# 11. Redirections locales + profil de performance (RdpOptions) : FAIT —
#     s'appuie entièrement sur les plugins de canaux déjà fournis par
#     libfreerdp-client (rdpdr pour disques/imprimantes/série/parallèle/
#     smartcards, urbdrc pour USB, rdpsnd pour l'audio) plutôt que de
#     réimplémenter ces protocoles. asyncrdp_load_addins() les charge
#     selon les settings, à appeler après TOUS les settings et avant
#     connect(). Sélection fine par imprimante (RDPDR_PRINTER), ports
#     série/parallèle (RDPDR_SERIAL/RDPDR_PARALLEL) et multi-écran
#     (MonitorDefArray + UseMultimon) tous couverts explicitement — plus
#     de repli sur un simple toggle tout-ou-rien.
#     USB : l'énumération des périphériques (libusb) et la construction
#     des chaînes de sélection restent à la charge de l'appelant (GCM) —
#     ce shim ne fait que transmettre la chaîne à urbdrc (transmettre les
#     device_args, pas énumérer soi-même les bus USB, n'est pas une
#     limitation fonctionnelle : c'est la même couche que libusb assure
#     déjà mieux qu'une réimplémentation dans ce shim ne le ferait).
#
# 12. VALIDATION RÉELLE (sandbox Ubuntu 24.04, FreeRDP 3.30.0, serveur
#     xrdp local) : connexion, frames, clavier/souris, resize, clipboard
#     texte bidirectionnel — tous testés avec un vrai aller-retour
#     protocolaire (annonce → ServerFormatDataRequest → réponse → nouvelle
#     demande → contenu identique récupéré). Trois bugs réels trouvés et
#     corrigés au passage, aucun n'aurait été visible sans compiler et
#     exécuter réellement :
#       - asyncrdp_context_new() utilisait freerdp_new()+freerdp_context_new()
#         bruts au lieu de freerdp_client_context_new() — conséquence :
#         AUCUN canal ne se connectait jamais (confirmé sur 15s d'attente
#         active), pas seulement rdpdr comme d'abord soupçonné.
#       - asyncrdp_add_drive/printer/serial_port construits à la main
#         (calloc + remplissage de champs) crashaient à la libération —
#         il fallait passer par freerdp_device_new(), confirmé par trace
#         gdb complète pointant vers freerdp_addin_argv_free.
#       - _on_fd_readable levait une exception depuis un callback
#         loop.add_reader (jamais awaited donc jamais rattrapée) sans
#         désenregistrer les fds, causant une boucle d'erreurs infinie à
#         toute déconnexion en cours de session — corrigé en stockant
#         l'erreur dans self.last_error et en appelant _unregister_fds().
#
#     CAMPAGNE DE TESTS PAR REDIRECTION (suite, même sandbox) — chaque
#     redirection testée individuellement avec de VRAIES données, pas
#     seulement une connexion qui ne crash pas :
#       - Clipboard texte  : OK — contenu changé côté session distante
#         (xclip sur le vrai X11 de la session xrdp) → notification reçue
#         côté client, contenu identique. Point d'usage important : le
#         serveur ne renvoie le presse-papier que sur un CHANGEMENT détecté
#         après la connexion, pas l'état déjà présent avant — GCM devra
#         forcer une resynchronisation à l'ouverture d'un onglet plutôt que
#         de compter sur un état initial automatique.
#       - Clipboard image  : OK après correction d'un vrai bug — le DIB
#         reçu d'un vrai serveur avait un BITMAPV5HEADER (124 octets) et
#         pas le BITMAPINFOHEADER classique (40 octets) supposé au départ ;
#         gcm_gtk4_clipboard_bridge.py lisait déjà la taille dynamiquement
#         donc ce point passait, MAIS _dib_to_texture() calculait mal
#         bfOffBits pour les images en couleurs indexées (≤8bpp) en
#         ignorant la table de couleurs intercalée — confirmé par diff
#         octet à octet (1 seul octet faux, sur ce champ précis), corrigé,
#         revalidé byte-identique avec l'original.
#       - Clipboard fichiers : OK — liste de fichiers annoncée côté serveur
#         (xclip target text/uri-list) → métadonnées exactes reçues (nom,
#         taille) ET contenu réel téléchargé via
#         request_remote_file_contents(), identique à l'original.
#       - Disque redirigé : OK, dans les DEUX sens — fichier créé côté
#         client visible et lisible depuis la session distante (montage
#         FUSE réel sous ~/thinclient_drives) ; fichier écrit depuis la
#         session distante apparu instantanément côté client, contenu
#         identique. Un vrai problème d'environnement trouvé au passage :
#         /dev/fuse était en mode 600 (root uniquement), empêchant
#         xrdp-chansrv (qui tourne sous l'utilisateur de session) de créer
#         le montage — sans lien avec notre code, mais bloquant tant que
#         non corrigé (chmod 666 /dev/fuse dans ce sandbox).
#       - Audio : négociation de canal confirmée (formats échangés, round
#         trip time mesuré côté chansrv), mais aucun octet audio réel
#         transféré — ce sandbox n'a aucun sous-système ALSA/PipeWire, donc
#         rien ne peut réellement émettre de son côté session pour tester
#         la charge utile, seulement la couche protocole.
#       - Imprimante, série, parallèle : le CLIENT annonce/enregistre
#         correctement le device dans tous les cas (logs FreeRDP confirmés
#         via device_announce), mais xrdp-chansrv répond explicitement
#         "(not supported)" dans les logs pour les trois — LIMITATION DU
#         SERVEUR DE TEST (xrdp), pas du code client. Impossible de valider
#         plus loin qu'un "le client parle bien le protocole" sans un
#         serveur RDP supportant réellement ces canaux (ex: un vrai Windows
#         Server, ou un autre serveur Linux avec un backend CUPS/série
#         complet).
#       - Multi-écran : OK — deux moniteurs virtuels déclarés
#         (1280x800 + 1024x768), résolution totale bounding-box calculée
#         (2304x800) acceptée par le serveur, VRAIE frame reçue à cette
#         résolution exacte avec la taille en octets attendue
#         (2304*800*4 = 7 372 800, sans padding de stride ici).
#
#     Non testé malgré tout ce travail : USB (nécessiterait un vrai
#     périphérique et un serveur qui le supporte), GTK4 (affichage + les
#     trois ponts clipboard) — jamais exécuté faute d'environnement
#     graphique local pour lancer une fenêtre GTK4 dans ce sandbox.
# ---------------------------------------------------------------------------
