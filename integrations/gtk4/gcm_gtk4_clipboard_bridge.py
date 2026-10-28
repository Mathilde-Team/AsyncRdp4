# SPDX-License-Identifier: GPL-3.0-or-later
"""
gcm_gtk4_clipboard_bridge.py

Câble asyncrdp.Client.clipboard (texte, image, fichiers — cf.
asyncrdp.py) sur le presse-papier GTK4 natif (Gdk.Clipboard),
pour intégration dans GCM.

GTK4 a lui-même une API de presse-papier asynchrone (read_text_async /
read_texture_async / read_value_async / set()) — ce pont ne fait que
relier les deux mondes async (asyncio côté asyncrdp, GLib.MainLoop côté
GTK4) sans bloquer ni l'un ni l'autre.

Suppose que GCM tourne asyncio et GTK4 sur le MÊME thread, via un
intégrateur de boucle (ex: gbulb, ou glib.MainLoop imbriquée dans
asyncio via une policy dédiée) — c'est déjà nécessaire pour toute
appli GTK4 utilisant asyncio ailleurs dans GCM (ex: asyncvnc2).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import struct
import tempfile
from pathlib import Path

import gi
import numpy as np
from gi.repository import Gdk, GdkPixbuf, Gio, GLib  # type: ignore
from loguru import logger

from asyncrdp import Client as RdpClient
from asyncrdp import RemoteFileInfo
from asyncrdp.tracing import traced

# Dossier de build de integrations/gtk4/native/ (voir build_gir.py à la
# racine du projet) — une petite bibliothèque GObject Introspection en C
# pur, distincte du binding cffi principal.
_NATIVE_BUILD_DIR = Path(__file__).resolve().parent / "native" / "_build"


@traced
def _load_native_image_provider():
    """
    Charge AsyncrdpClipboard.ImageContentProvider si build_gir.py a été
    exécuté (voir integrations/gtk4/native/asyncrdp-image-provider.h pour
    le détail complet du problème contourné) : un GdkContentProvider écrit
    en C pur, jamais surchargé côté Python, qui échappe à un bug de
    marshaling PyGObject précis — do_write_mime_type_async() d'une
    sous-classe *Python* de Gdk.ContentProvider reçoit un user_data déjà
    perdu (`None`) avant même d'être appelée, confirmé par lecture de
    gdk/gdkclipboard.c et expérimentation directe le 2026-09-11 (voir
    CLAUDE.md, docs/sessions/session-24.md et session-25.md). Confirmé par
    un vrai lecteur externe (xclip) recevant un vrai PNG le 2026-09-14
    (docs/sessions/session-27.md), là où trois stratégies d'écriture
    différentes en PyGObject échouaient toutes à l'identique.

    Retourne None si le typelib n'existe pas (build_gir.py jamais exécuté)
    ou si son chargement échoue pour une autre raison — dégradation
    propre vers Gdk.ContentProvider.new_for_value() dans
    _on_remote_image_changed ci-dessous (mime-types vides pour un lecteur
    externe, même limitation que documentée depuis le 2026-09-02), jamais
    une erreur bloquante pour le reste du pont.
    """
    if not (_NATIVE_BUILD_DIR / "AsyncrdpClipboard-1.0.typelib").is_file():
        logger.debug(
            "AsyncrdpClipboard : typelib absent ({}), build_gir.py n'a probablement "
            "pas été exécuté -- repli sur Gdk.ContentProvider.new_for_value()",
            _NATIVE_BUILD_DIR,
        )
        return None
    if not hasattr(gi, "require_version"):
        # `gi` n'est pas le vrai PyGObject (cas de tests/gtk_stub/gi/, utilisé par
        # test_gtk4_bridges.py pour tourner sans aucun GTK4 réel installé) : même si
        # le typelib existe sur disque (build_gir.py déjà exécuté dans ce même
        # checkout pour un test réel), il n'y a pas de vrai gi-repository ici pour le
        # charger. Ce n'est pas une erreur -- exactement le cas que ce stub est censé
        # simuler (logique pure, sans dépendance GTK4/GObject-Introspection réelle).
        return None
    try:
        # Volontairement PAS os.environ["GI_TYPELIB_PATH"] = ... : GObject-
        # Introspection ne lit cette variable qu'une seule fois, à la
        # toute première opération de la Repository par défaut du process
        # (le tout premier gi.require_version(), quel que soit le
        # namespace). Dans une vraie application GTK4 -- ou simplement
        # dans cette suite de tests, où _require_gtk4() a déjà chargé Gtk
        # bien avant que ce module ne soit importé -- ce premier appel a
        # déjà eu lieu, et la variable d'environnement arrive trop tard :
        # confirmé par un vrai échec (ValueError, PAS un repli silencieux)
        # dans test_clipboard_image_available_to_external_reader avant ce
        # correctif (session-27.md). GIRepository.Repository.
        # prepend_search_path() modifie la même liste de recherche mais à
        # chaud, à l'appel, quel que soit ce qui a déjà été chargé avant --
        # c'est l'API prévue pour exactement ce cas (typelib hors des
        # emplacements système). GIRepository-2.0 lui-même est installé à
        # un emplacement système standard, jamais concerné par ce problème.
        search_path = str(_NATIVE_BUILD_DIR)
        # PyGObject >= 3.52 s'appuie sur libgirepository-2.0, dont le dépôt
        # n'est plus un singleton : GIRepository.Repository.
        # prepend_search_path() n'atteint plus le dépôt utilisé par `gi`
        # (« Namespace AsyncrdpClipboard not available », constaté le
        # 2026-10-11 avec PyGObject 3.58, issue #41). Ces versions exposent
        # le dépôt réellement utilisé : gi.Repository.get_default().
        pygobject_repo = getattr(gi, "Repository", None)
        if pygobject_repo is not None and hasattr(pygobject_repo.get_default(), "prepend_search_path"):
            pygobject_repo.get_default().prepend_search_path(search_path)
        else:
            gi.require_version("GIRepository", "2.0")
            from gi.repository import GIRepository

            if search_path not in GIRepository.Repository.get_search_path():
                GIRepository.Repository.prepend_search_path(search_path)

        gi.require_version("AsyncrdpClipboard", "1.0")
        from gi.repository import AsyncrdpClipboard

        return AsyncrdpClipboard
    except (ImportError, ValueError) as exc:
        logger.warning(
            "AsyncrdpClipboard (provider presse-papier image natif) trouvé mais non "
            "chargeable, repli sur Gdk.ContentProvider.new_for_value() (mime-types "
            "vides pour un lecteur externe, voir CLAUDE.md) : {}",
            exc,
        )
        return None


_AsyncrdpClipboardNative = _load_native_image_provider()


@traced
def _pixbuf_to_dib(pixbuf: GdkPixbuf.Pixbuf) -> bytes:
    """
    Construit un DIB brut (BITMAPINFOHEADER 24bpp + pixels BGR, lignes de
    bas en haut, alignées sur 4 octets) à partir d'un GdkPixbuf.Pixbuf.

    Écrit à la main plutôt que de dépendre du saver BMP de gdk-pixbuf,
    dont la disponibilité (module de sauvegarde bmp) varie selon les
    distributions — la lecture BMP, elle, est quasi universelle (voir
    _dib_to_texture ci-dessous qui s'appuie dessus sans risque).

    Vectorisé avec numpy (slicing/flip/reshape) plutôt qu'une boucle
    Python pixel par pixel — le gain est significatif sur des images de
    résolution bureau (l'ancienne version pur-Python devenait le goulot
    d'étranglement dès quelques centaines de milliers de pixels).
    """
    width = pixbuf.get_width()
    height = pixbuf.get_height()
    n_channels = pixbuf.get_n_channels()
    rowstride = pixbuf.get_rowstride()

    raw = np.frombuffer(pixbuf.get_pixels(), dtype=np.uint8)
    raw = raw[: rowstride * height].reshape(height, rowstride)
    rgb = raw[:, : width * n_channels].reshape(height, width, n_channels)[:, :, :3]

    bgr = rgb[:, :, ::-1]  # RGB -> BGR
    bgr = np.flipud(bgr)  # DIB : lignes de bas en haut

    row_size = ((width * 3 + 3) // 4) * 4
    body = np.zeros((height, row_size), dtype=np.uint8)  # initialisé à 0 : gère le padding de fin de ligne
    body[:, : width * 3] = bgr.reshape(height, width * 3)

    header = struct.pack(
        "<IiiHHIIiiII",
        40,  # biSize (BITMAPINFOHEADER)
        width,  # biWidth
        height,  # biHeight (positif = bottom-up)
        1,  # biPlanes
        24,  # biBitCount
        0,  # biCompression (BI_RGB)
        body.size,  # biSizeImage
        0,
        0,  # biXPelsPerMeter, biYPelsPerMeter
        0,
        0,  # biClrUsed, biClrImportant
    )
    return header + body.tobytes()


@traced
def _dib_to_texture(dib: bytes) -> Gdk.Texture | None:
    """
    Reconstruit un fichier BMP complet (BITMAPFILEHEADER 14 octets +
    le DIB reçu tel quel) pour le faire décoder par GdkPixbuf, qui sait
    quasi universellement LIRE du BMP (contrairement à l'écriture, cf.
    _pixbuf_to_dib ci-dessus).
    """
    if len(dib) < 4:
        return None
    (header_size,) = struct.unpack_from("<I", dib, 0)

    # BUG RÉEL trouvé en testant contre un vrai serveur RDP (xrdp) avec une
    # image en couleurs indexées (4bpp) : entre le header DIB et les
    # données de pixels se trouve une table de couleurs (palette) pour
    # toute profondeur <= 8bpp — l'ignorer produit un bfOffBits faux.
    # Confirmé octet par octet : un seul octet de différence entre le BMP
    # reconstruit et l'original, exactement sur ce champ ; les pixels
    # eux-mêmes étaient corrects (GdkPixbuf/ImageMagick semblent recalculer
    # l'offset réel plutôt que de faire confiance à bfOffBits déclaré), mais
    # rien ne garantit qu'un décodeur plus strict tolère la même erreur.
    if header_size >= 40 and len(dib) >= 36:
        (bit_count,) = struct.unpack_from("<H", dib, 14)
        (colors_used,) = struct.unpack_from("<I", dib, 32)
    else:
        bit_count, colors_used = 24, 0

    if bit_count <= 8:
        num_colors = colors_used if colors_used else (1 << bit_count)
        palette_size = num_colors * 4  # RGBQUAD = 4 octets par couleur
    else:
        palette_size = 0

    pixel_offset = 14 + header_size + palette_size
    file_header = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, pixel_offset)
    bmp_bytes = file_header + dib

    try:
        loader = GdkPixbuf.PixbufLoader.new_with_type("bmp")
        loader.write(bmp_bytes)
        loader.close()
        pixbuf = loader.get_pixbuf()
    except GLib.Error as exc:
        logger.warning("Clipboard bridge: DIB reçu non décodable en image ({})", exc)
        return None
    if pixbuf is None:
        return None
    return Gdk.Texture.new_for_pixbuf(pixbuf)


class ClipboardBridge:
    """Un pont par session RDP active (un onglet GCM = un pont)."""

    @traced
    def __init__(self, rdp_client: RdpClient, gdk_display: Gdk.Display):
        self._rdp = rdp_client
        self._clipboard = gdk_display.get_clipboard()
        self._suppress_next_local_change = False

        # Répertoire temporaire pour matérialiser les fichiers distants
        # avant de les proposer au presse-papier local — Gdk.FileList
        # attend des GFile déjà existants, pas une source à la demande.
        self._download_dir = Path(tempfile.mkdtemp(prefix="gcm-rdp-clipboard-"))
        # Référence forte sur la tâche "fire-and-forget" de téléchargement RDP -> local
        # (voir `_on_remote_files_changed` plus bas) : sans ça, l'event loop ne garde
        # qu'une référence faible et peut garbage-collecter la tâche en plein transfert.
        self._background_tasks: set[asyncio.Task] = set()

        self._rdp.clipboard.on_remote_text_changed = self._on_remote_text_changed
        self._rdp.clipboard.on_remote_image_changed = self._on_remote_image_changed
        self._rdp.clipboard.on_remote_files_changed = self._on_remote_files_changed

        self._rdp.clipboard.get_local_text = self._get_local_text_sync
        self._rdp.clipboard.get_local_image_dib = self._get_local_image_dib_sync
        self._rdp.clipboard.get_local_files = self._get_local_files_sync

        self._clipboard.connect("changed", self._on_local_clipboard_changed)

        self._local_text_cache: str = ""
        self._local_image_dib_cache: bytes | None = None
        self._local_files_cache: list[str] = []

    # ------------------------------------------------------------------
    # RDP -> local
    # ------------------------------------------------------------------

    @traced
    def _on_remote_text_changed(self, text: str) -> None:
        logger.debug("Clipboard bridge: RDP -> local (texte), {} caractères", len(text))
        self._local_text_cache = text
        self._suppress_next_local_change = True
        self._clipboard.set(text)

    @traced
    def _on_remote_image_changed(self, dib: bytes) -> None:
        texture = _dib_to_texture(dib)
        if texture is None:
            return
        logger.debug("Clipboard bridge: RDP -> local (image), {} octets", len(dib))
        self._local_image_dib_cache = dib
        self._suppress_next_local_change = True
        if _AsyncrdpClipboardNative is not None:
            # Provider natif C (voir integrations/gtk4/native/) : seul à
            # offrir réellement l'image (mime-type ET octets) à un lecteur
            # externe sur ce GTK4/PyGObject -- voir _load_native_image_provider.
            provider = _AsyncrdpClipboardNative.ImageContentProvider.new(texture)
        else:
            provider = Gdk.ContentProvider.new_for_value(texture)
        self._clipboard.set_content(provider)

    @traced
    def _on_remote_files_changed(self, files: list[RemoteFileInfo]) -> None:
        """
        Télécharge immédiatement tous les fichiers annoncés dans un
        répertoire temporaire (en respectant l'arborescence transmise par
        le serveur — les dossiers sont recréés, pas juste ignorés), puis
        propose le résultat au presse-papier local en tant que vrais
        fichiers (Gdk.FileList attend des GFile existants — pas de
        matérialisation paresseuse possible ici).

        Les transferts sont parallélisés (borne à 4 en simultané) plutôt
        que séquentiels : pour une sélection de nombreux petits fichiers,
        c'est la latence réseau par fichier qui dominait, pas la bande
        passante.

        LIMITATION ASSUMÉE : pour une sélection très volumineuse, ce
        téléchargement bloque quand même la disponibilité du presse-papier
        local pendant sa durée totale — une UI réelle voudrait
        probablement une barre de progression plutôt qu'une attente
        silencieuse.
        """
        logger.info("Clipboard bridge: RDP -> local (fichiers), {} entrée(s) annoncée(s)", len(files))
        task = asyncio.ensure_future(self._download_and_set_files(files))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @traced
    async def _download_and_set_files(self, files: list[RemoteFileInfo]) -> None:
        semaphore = asyncio.Semaphore(4)

        @traced
        async def fetch_one(info: RemoteFileInfo) -> str | None:
            # info.name utilise '\\' comme séparateur (convention
            # MS-RDPECLIP) — à convertir vers le séparateur local.
            rel_path = info.name.replace("\\", os.sep)
            dest = self._download_dir / rel_path

            if info.is_directory:
                dest.mkdir(parents=True, exist_ok=True)
                # BUG RÉEL trouvé en testant contre un vrai presse-papier GTK4
                # avec un lecteur externe (xclip -t text/uri-list) : renvoyer
                # None ici (comme avant ce correctif) exclut silencieusement
                # tout dossier de premier niveau de la Gdk.FileList locale,
                # via le filtre `path is not None` plus bas — alors que son
                # contenu, lui, est bien recréé sur disque. Résultat concret :
                # copier un dossier + un fichier isolé depuis RDP puis coller
                # localement ne faisait apparaître QUE le fichier isolé, le
                # dossier (et tout ce qu'il contient) restant invisible dans
                # un répertoire temporaire que l'utilisateur ne voit jamais.
                # Le dossier existe déjà réellement sur disque à ce stade
                # (mkdir ci-dessus) : rien n'empêche de le proposer comme un
                # GFile de plus, exactement comme un fichier.
                return str(dest)

            dest.parent.mkdir(parents=True, exist_ok=True)
            async with semaphore:
                data = await self._rdp.clipboard.request_remote_file_contents(info.index)
            dest.write_bytes(data)
            logger.debug("Clipboard bridge: {!r} téléchargé ({} octets)", info.name, len(data))
            return str(dest)

        results = await asyncio.gather(*(fetch_one(info) for info in files))

        # Seules les entrées de premier niveau (sans '\\' dans le nom
        # annoncé) vont dans la Gdk.FileList — le contenu imbriqué existe
        # déjà sur disque en dessous, GTK4/Nautilus s'attend à recevoir le
        # dossier racine, pas chacun de ses fichiers individuellement.
        local_paths = [
            path for info, path in zip(files, results, strict=True) if path is not None and "\\" not in info.name
        ]
        if not local_paths:
            return

        gfiles = [Gio.File.new_for_path(p) for p in local_paths]
        file_list = Gdk.FileList.new_from_list(gfiles)
        self._suppress_next_local_change = True
        self._clipboard.set_content(Gdk.ContentProvider.new_for_value(file_list))

    # ------------------------------------------------------------------
    # local -> RDP
    # ------------------------------------------------------------------

    @traced
    def _on_local_clipboard_changed(self, clipboard: Gdk.Clipboard) -> None:
        if self._suppress_next_local_change:
            self._suppress_next_local_change = False
            return

        formats = clipboard.get_formats()
        if formats.contain_gtype(Gdk.FileList):
            logger.debug("Clipboard bridge: changement local détecté (fichiers)")
            clipboard.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, self._on_local_files_read)
        elif formats.contain_gtype(Gdk.Texture):
            logger.debug("Clipboard bridge: changement local détecté (image)")
            clipboard.read_texture_async(None, self._on_local_texture_read)
        else:
            logger.debug("Clipboard bridge: changement local détecté (texte)")
            clipboard.read_text_async(None, self._on_local_text_read)

    @traced
    def _on_local_text_read(self, clipboard: Gdk.Clipboard, result: object) -> None:
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error as exc:
            logger.debug("Clipboard bridge: lecture locale (texte) impossible ({}), ignoré", exc)
            return
        if text is None:
            return
        self._local_text_cache = text
        logger.debug("Clipboard bridge: local -> RDP (texte), {} caractères", len(text))
        self._rdp.clipboard.announce_local_text(text)

    @traced
    def _on_local_texture_read(self, clipboard: Gdk.Clipboard, result: object) -> None:
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error as exc:
            logger.debug("Clipboard bridge: lecture locale (image) impossible ({}), ignoré", exc)
            return
        if texture is None:
            return
        pixbuf = Gdk.pixbuf_get_from_texture(texture)
        if pixbuf is None:
            logger.warning("Clipboard bridge: conversion texture -> pixbuf échouée")
            return
        dib = _pixbuf_to_dib(pixbuf)
        self._local_image_dib_cache = dib
        logger.debug("Clipboard bridge: local -> RDP (image), {} octets", len(dib))
        self._rdp.clipboard.announce_local_image()

    @traced
    def _on_local_files_read(self, clipboard: Gdk.Clipboard, result: object) -> None:
        try:
            file_list = clipboard.read_value_finish(result)
        except GLib.Error as exc:
            logger.debug("Clipboard bridge: lecture locale (fichiers) impossible ({}), ignoré", exc)
            return
        paths = [f.get_path() for f in file_list.get_files() if f.get_path()]
        if not paths:
            return
        self._local_files_cache = paths
        logger.debug("Clipboard bridge: local -> RDP (fichiers), {} entrée(s)", len(paths))
        self._rdp.clipboard.announce_local_files(paths)

    # ------------------------------------------------------------------
    # Réponses synchrones attendues par asyncrdp.Clipboard (voir la note
    # dans _get_local_text_sync — même limitation structurelle pour les
    # trois : le serveur RDP attend une réponse immédiate, alors que les
    # lectures GTK4 sont asynchrones. On sert donc toujours depuis un
    # cache tenu à jour par les callbacks *_read ci-dessus.
    # ------------------------------------------------------------------

    @traced
    def _get_local_text_sync(self) -> str:
        return self._local_text_cache

    @traced
    def _get_local_image_dib_sync(self) -> bytes | None:
        return self._local_image_dib_cache

    @traced
    def _get_local_files_sync(self) -> list[str]:
        return self._local_files_cache

    @traced
    def close(self) -> None:
        """À appeler quand l'onglet GCM correspondant se ferme."""
        self._rdp.clipboard.on_remote_text_changed = None
        self._rdp.clipboard.on_remote_image_changed = None
        self._rdp.clipboard.on_remote_files_changed = None
        self._rdp.clipboard.get_local_text = None
        self._rdp.clipboard.get_local_image_dib = None
        self._rdp.clipboard.get_local_files = None
        # Nettoyage best-effort du répertoire temporaire de téléchargement.
        for f in self._download_dir.glob("*"):
            with contextlib.suppress(OSError):
                f.unlink()
        with contextlib.suppress(OSError):
            self._download_dir.rmdir()
        logger.debug("Clipboard bridge fermé")
