# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests « live » des vrais ponts GTK4 (integrations/gtk4/gcm_gtk4_*_bridge.py)
— PAS les doublures de tests/gtk_stub/. Contrairement à test_gtk4_bridges.py
(qui tourne partout, y compris sans aucun GTK4 installé, grâce au stub),
ces tests importent les vrais modules et ont donc besoin d'un vrai GTK4,
d'un vrai Gdk.Display et de quelques utilitaires X11 externes.

MISE EN PLACE REQUISE (rien de tout cela n'est nécessaire pour le reste
de la suite — uniquement pour ce fichier) :

    apt-get install -y gir1.2-gtk-4.0 xvfb xclip x11-apps imagemagick
    # asyncio/GLib : gi.events (PyGObject >= 3.50) ; gbulb seulement en repli
    # (gbulb ne s'importe plus sous Python 3.14), voir gcm_gtk4_asyncio.py
    Xvfb :99 -screen 0 400x300x24 -nolisten tcp &
    export DISPLAY=:99 GDK_BACKEND=x11 ASYNCRDP_TEST_GTK4=1
    pytest tests/test_gtk4_live.py -v

Sans ASYNCRDP_TEST_GTK4=1, tous les tests de ce fichier sont `skip`
proprement (voir fixture gtk4_live_enabled dans conftest.py).

Sur Mesa/Xvfb sans DRI3 exploitable (constaté le 2026-09-06, voir
CLAUDE.md), le tout premier widget GTK4 construit segfault — ajouter
`LIBGL_ALWAYS_SOFTWARE=1` à l'environnement ci-dessus si ça arrive
(force le rendu logiciel Mesa, aucun changement de code requis).

Constat distinct et plus radical le 2026-09-18 : sur un Xvfb dont le
paquet ne fournit AUCUN module GLX (`dpkg -L xvfb | grep glx` vide —
pas juste DRI3 indisponible), `Gdk.Display.open()` /
`Gdk.Display.get_default()` renvoient silencieusement `None` (aucune
exception, rien via `GDK_DEBUG=all`), y compris depuis l'intérieur d'un
`Gtk.Application.run()` — alors qu'un client Xlib pur (`xdpyinfo`,
`xterm`) se connecte sans aucun problème au même display. Contrairement
au cas DRI3 ci-dessus, `LIBGL_ALWAYS_SOFTWARE=1` ne change rien : il
n'y a tout simplement pas d'extension GLX à secourir par un rendu
logiciel. Aucun correctif possible côté ce projet dans ce cas — seule
une image Xvfb compilée avec GLX (ou un autre serveur X virtuel)
débloquerait un vrai test de fenêtre GTK4. Le sandbox de développement
n'étant pas persistant (voir docs/test-environment.md), cette
limitation précise peut ou non se reproduire d'une session à l'autre —
à revérifier (`dpkg -L xvfb | grep glx` ; en complément, un test plus
direct et déjà rencontré comme fiable le 2026-09-18 (session 32) :
lancer Xvfb puis `DISPLAY=:99 xdpyinfo -queryExtensions | grep -i GLX`)
plutôt qu'à supposer réglée ou présente par défaut.

**Confirmé variable le 2026-09-18 (session 32, nouveau bac à sable)** :
contrairement à la session 31 citée ci-dessus, le Xvfb de CE bac à
sable expose bel et bien GLX (même paquet `xvfb` Ubuntu 24.04 —
`dpkg -L xvfb | grep glx` reste vide ici aussi, ce n'est donc pas un
indicateur fiable ; c'est `xdpyinfo -queryExtensions` qui tranche) —
`test_demo_session_assembles_real_window_display_and_clipboard_bridge`
passe réellement, voir son docstring dédié plus bas et
`docs/sessions/session-32.md`. Confirme qu'il faut revérifier à chaque
session plutôt que supposer le blocage permanent.

Piège rencontré en écrivant ces tests, à ne pas réintroduire :
tout appel bloquant (subprocess.run, ou un GLib.MainLoop imbriqué) fait
depuis l'intérieur de notre propre application GTK4 alors qu'un client
X11 externe (xclip) attend une réponse DE CETTE MÊME application est un
deadlock garanti — l'appli ne peut pas répondre à la requête de
sélection tant qu'elle est bloquée dans cet appel. Tout ce qui interroge
notre propre presse-papier depuis l'extérieur doit passer par
subprocess.Popen + un poll périodique via GLib.timeout_add.

Constat fait le 2026-09-02 et documenté dans CLAUDE.md : dans cet
environnement, GDK ne déclare aucun mime-type sérialisable pour un
GdkTexture via Gdk.ContentProvider.new_for_value() (ni image/png, ni
image/bmp, alors que les deux formats gdk-pixbuf sont bel et bien
présents et « writable »), et toute sous-classe *Python* de
Gdk.ContentProvider qui tente de corriger ça se heurte à un bug de
marshaling PyGObject distinct et plus profond (user_data perdu dans
do_write_mime_type_async avant même d'être appelée — voir les deux
tests xfail dédiés plus bas et docs/pygobject-async-vfunc-userdata-bug-
report.md). Le décodage RDP -> texture (le vrai sujet historiquement
bogué, cf. bfOffBits) fonctionne bien avec le vrai GdkPixbuf ; c'est
uniquement l'étape suivante, l'offre de cette image via le presse-papier
système à un lecteur EXTERNE, qui posait problème.

Résolu le 2026-09-14 (docs/sessions/session-27.md) par une sous-classe
de Gdk.ContentProvider écrite en C pur plutôt qu'en PyGObject
(integrations/gtk4/native/, voir build_gir.py) : jamais surchargée côté
Python, elle échappe entièrement au marshaling en cause.
test_clipboard_image_available_to_external_reader (le test qui passe
par le VRAI ClipboardBridge livré) et
test_clipboard_image_native_c_provider_delivers_real_png (l'expérience
isolée sur le mécanisme lui-même) vérifient tous les deux, par un
lecteur externe réel, que ce n'est plus un xfail. Les deux tentatives
PyGObject précédentes restent en xfail : elles documentent toujours
fidèlement une impasse réelle, seulement contournée, pas corrigée.
"""

import struct
import subprocess
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.gtk4_live

_GTK4_DIR = str(Path(__file__).resolve().parent.parent / "integrations" / "gtk4")


def _require_gtk4():
    sys.path.insert(0, _GTK4_DIR)
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    return Gtk, Gdk, GLib


def _import_display_and_session_with_stub_asyncrdp():
    """Charge les VRAIS gcm_gtk4_display_bridge / gcm_gtk4_clipboard_bridge
    / gcm_gtk4_rdp_session — leurs propres `from gi.repository import ...`
    résolus contre le vrai GTK4 tout juste requis par _require_gtk4() (ce
    test a besoin d'un vrai Gdk.Display, contrairement à
    test_gtk4_bridges.py) — mais avec `asyncrdp` remplacé par la même
    doublure que tests/gtk_stub/asyncrdp.py : seuls les NOMS Client/
    RemoteFileInfo sont nécessaires à l'import de ces trois fichiers, pas
    le binding cffi compilé contre FreeRDP — la couche protocole RDP est
    de toute façon simulée dans le test qui appelle ce helper
    (_FakeConnectFn plus bas), qui ne porte que sur l'assemblage GTK4
    lui-même. asyncrdp.tracing, en revanche, est chargé pour de vrai
    (module pur Python, sans aucune dépendance cffi, voir sa docstring) :
    le décorateur @traced réellement appliqué aux trois fichiers est donc
    le même qu'en production, pas une doublure qui l'ignorerait
    silencieusement.

    Restaure sys.modules après coup pour gcm_gtk4_clipboard_bridge et
    gcm_gtk4_rdp_session (deux classes Python ordinaires, comme
    test_gtk4_bridges.py::_import_bridges_with_stub()) — PAS pour
    gcm_gtk4_display_bridge, voir le commentaire dédié plus bas."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent
    stub_asyncrdp_path = Path(__file__).resolve().parent / "gtk_stub" / "asyncrdp.py"
    gtk4_dir = Path(_GTK4_DIR)

    def _load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    # gcm_gtk4_display_bridge.RdpView enregistre son GType GObject au
    # moment même de la définition de la classe (`class RdpView(Gtk.
    # Widget)`) — un GType est global au process et ne peut jamais être
    # réenregistré sous le même nom, même après avoir restauré
    # sys.modules ensuite : le restaurer ne défait pas l'enregistrement
    # déjà fait côté C. Trouvé le 2026-09-18 (session 32) en lançant ce
    # test juste après test_display_bridge_renders_correct_colors_onscreen
    # dans le même process pytest (celui-ci fait un `from
    # gcm_gtk4_display_bridge import RdpView` bien réel, sans passer par
    # ce helper) : la ligne `finally` ci-dessous rechargeait
    # inconditionnellement une SECONDE définition de la classe, avec
    # `RuntimeError: could not create new GType: RdpView` à la clé.
    # RdpView (contrairement à ClipboardBridge/RdpSession juste en
    # dessous, deux classes Python ordinaires sans GType) est donc traité
    # à part, dans les deux sens : s'il est déjà dans sys.modules (chargé
    # pour de vrai par un autre test, ou par un appel précédent de ce
    # même helper), on le réutilise tel quel sans rejouer sa définition ;
    # et s'il est chargé ici pour la première fois, il est délibérément
    # laissé résident après coup (jamais restauré/retiré de sys.modules,
    # contrairement aux autres clés plus bas) pour qu'un futur `from
    # gcm_gtk4_display_bridge import RdpView` ailleurs dans le même
    # process le retrouve via le cache normal de Python plutôt que de
    # retenter, lui aussi, un enregistrement. Sans conséquence dans les
    # deux cas : RdpView ne se sert de Client/RemoteFileInfo d'asyncrdp
    # qu'en annotation de type, jamais au runtime.
    if "gcm_gtk4_display_bridge" in sys.modules:
        display_bridge = sys.modules["gcm_gtk4_display_bridge"]
    else:
        keys = ("asyncrdp", "asyncrdp.tracing")
        saved = {key: sys.modules.get(key) for key in keys}
        try:
            _load("asyncrdp", stub_asyncrdp_path)
            _load("asyncrdp.tracing", repo_root / "src" / "asyncrdp" / "tracing.py")
            display_bridge = _load("gcm_gtk4_display_bridge", gtk4_dir / "gcm_gtk4_display_bridge.py")
        finally:
            for key, mod in saved.items():
                if mod is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = mod

    keys = ("asyncrdp", "asyncrdp.tracing", "gcm_gtk4_clipboard_bridge", "gcm_gtk4_rdp_session")
    saved = {key: sys.modules.get(key) for key in keys}
    try:
        _load("asyncrdp", stub_asyncrdp_path)
        _load("asyncrdp.tracing", repo_root / "src" / "asyncrdp" / "tracing.py")
        _load("gcm_gtk4_clipboard_bridge", gtk4_dir / "gcm_gtk4_clipboard_bridge.py")
        rdp_session = _load("gcm_gtk4_rdp_session", gtk4_dir / "gcm_gtk4_rdp_session.py")
    finally:
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod
    return display_bridge.RdpView, rdp_session.RdpSession


def _run_async(GLib, cmd, on_done, input_bytes=None, timeout_s=6.0):
    """subprocess.Popen non bloquant + poll GLib — voir l'avertissement
    en tête de fichier sur le deadlock d'un appel bloquant équivalent.

    Quand `input_bytes` est fourni, xclip fork un démon qui hérite des
    fds stdout/stderr et les garde ouverts indéfiniment pour continuer à
    servir le presse-papier : les capturer avec PIPE ferait bloquer
    proc.stdout.read() pour toujours après la sortie du parent. D'où
    DEVNULL dans ce cas, PIPE seulement pour une lecture (xclip -o, qui
    ne démonise pas)."""
    daemonizes = input_bytes is not None
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input_bytes is not None else None,
        stdout=subprocess.DEVNULL if daemonizes else subprocess.PIPE,
        stderr=subprocess.DEVNULL if daemonizes else subprocess.PIPE,
    )
    if input_bytes is not None:
        proc.stdin.write(input_bytes)
        proc.stdin.close()
    ticks = [0]
    max_ticks = int(timeout_s / 0.1)

    def poll():
        ticks[0] += 1
        ret = proc.poll()
        if ret is not None:
            out = proc.stdout.read() if proc.stdout is not None else b""
            err = proc.stderr.read() if proc.stderr is not None else b""
            on_done(ret, out, err)
            return False
        if ticks[0] > max_ticks:
            proc.kill()
            on_done(None, b"", b"TIMEOUT")
            return False
        return True

    GLib.timeout_add(100, poll)


def _make_indexed_bmp_dib(width=4, height=2):
    """DIB 4bpp indexé (palette 16 couleurs) — rejoue le scénario exact
    du bug bfOffBits corrigé (voir CLAUDE.md), décodé ici par le VRAI
    GdkPixbuf plutôt que par le stub."""
    bit_count = 4
    num_colors = 16
    palette = b"".join(struct.pack("<BBBB", i * 16, i * 16, i * 16, 0) for i in range(num_colors))
    row_bytes = (width * bit_count + 7) // 8
    row_padded = ((row_bytes + 3) // 4) * 4
    row = bytearray(row_padded)
    row[0] = 0x0F  # deux pixels : index 0, index 15
    rows = bytes(row) * height
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        bit_count,
        0,
        len(rows),
        0,
        0,
        num_colors,
        0,
    )
    return header + palette + rows


def test_display_bridge_renders_correct_colors_onscreen(gtk4_live_enabled, tmp_path):
    """Pousse une frame BGRA32 de couleurs connues à travers la VRAIE
    RdpView._show_frame(), puis vérifie via une capture d'écran X11
    RÉELLE (xwd — lecture directe des pixels du serveur X, complètement
    indépendante de l'API GDK) que les couleurs affichées à l'écran sont
    correctes. Valide concrètement l'hypothèse de format documentée dans
    gcm_gtk4_display_bridge.py (Gdk.MemoryFormat.B8G8R8A8 pour du
    BGRA32 natif little-endian)."""
    Gtk, _Gdk, GLib = _require_gtk4()
    from gcm_gtk4_display_bridge import RdpView

    outcome = {}

    def on_activate(app):
        view = RdpView()
        win = Gtk.ApplicationWindow(application=app)
        win.set_child(view)
        win.set_default_size(40, 40)
        win.present()

        # 2x2 : chaque pixel occupe un quadrant visible une fois mis à
        # l'échelle en CONTAIN sur 40x40.
        raw = (
            bytes([0x00, 0x00, 0xFF, 0xFF])  # rouge (B,G,R,A)
            + bytes([0x00, 0xFF, 0x00, 0xFF])  # vert
            + bytes([0xFF, 0x00, 0x00, 0xFF])  # bleu
            + bytes([0x00, 0xFF, 0xFF, 0xFF])  # jaune
        )
        view._show_frame(raw, (2, 2))

        def do_screenshot():
            xwd_path = tmp_path / "screen.xwd"
            png_path = tmp_path / "screen.png"
            subprocess.run(["xwd", "-root", "-out", str(xwd_path)], check=True, timeout=5)
            subprocess.run(["convert", str(xwd_path), str(png_path)], check=True, timeout=5)
            outcome["png_path"] = png_path
            app.quit()

        # Laisse le temps au map + au premier frame-clock GTK4 de dessiner
        # réellement avant la capture.
        GLib.timeout_add(600, lambda: (do_screenshot(), False)[1])

    app = Gtk.Application(application_id="org.asyncrdp.test.display")
    app.connect("activate", on_activate)
    app.run(None)

    assert "png_path" in outcome, "la capture d'écran n'a pas abouti (voir sortie GTK ci-dessus)"

    from PIL import Image

    img = Image.open(outcome["png_path"]).convert("RGB")

    def close(rgb, expected, tol=40):
        return all(abs(a - b) <= tol for a, b in zip(rgb, expected, strict=True))

    assert close(img.getpixel((10, 10)), (255, 0, 0)), "quadrant haut-gauche devrait être rouge"
    assert close(img.getpixel((30, 10)), (0, 255, 0)), "quadrant haut-droite devrait être vert"
    assert close(img.getpixel((10, 30)), (0, 0, 255)), "quadrant bas-gauche devrait être bleu"
    assert close(img.getpixel((30, 30)), (255, 255, 0)), "quadrant bas-droite devrait être jaune"


def test_clipboard_bridge_text_roundtrip_via_external_client(gtk4_live_enabled):
    """Presse-papier système RÉEL, dans les deux sens, vérifié par un
    client X11 externe indépendant (xclip) — pas seulement depuis
    l'intérieur du même process GTK4."""
    Gtk, Gdk, GLib = _require_gtk4()
    from gcm_gtk4_clipboard_bridge import ClipboardBridge

    results = {}

    def on_activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.present()
        rdp = types.SimpleNamespace(clipboard=types.SimpleNamespace())
        bridge = ClipboardBridge(rdp, Gdk.Display.get_default())

        sent_text = "asyncrdp -> GTK4 : accents reels eac"
        bridge._on_remote_text_changed(sent_text)

        def after_read(ret, out, err):
            results["rdp_to_local"] = out.decode("utf-8", "replace")
            step2()

        GLib.timeout_add(
            300, lambda: (_run_async(GLib, ["xclip", "-o", "-selection", "clipboard"], after_read), False)[1]
        )

        def step2():
            captured = {}
            rdp.clipboard.announce_local_text = lambda t: captured.setdefault("text", t)
            external_text = "texte pose par un xclip externe"

            def after_write(ret, out, err):
                tick_count = 0

                def poll_capture():
                    nonlocal tick_count
                    tick_count += 1
                    if "text" in captured or tick_count > 50:
                        results["local_to_rdp"] = captured.get("text")
                        app.quit()
                        return False
                    return True

                GLib.timeout_add(100, poll_capture)

            _run_async(
                GLib, ["xclip", "-selection", "clipboard"], after_write, input_bytes=external_text.encode("utf-8")
            )
            results["_external_text_sent"] = external_text

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.text")
    app.connect("activate", on_activate)
    app.run(None)

    assert results.get("rdp_to_local") == "asyncrdp -> GTK4 : accents reels eac"
    assert results.get("local_to_rdp") == results.get("_external_text_sent")


def test_clipboard_bridge_decodes_real_indexed_bmp(gtk4_live_enabled):
    """Rejoue le scénario du bug bfOffBits (palette indexée 4bpp) avec le
    VRAI GdkPixbuf.PixbufLoader, pas le stub qui ne fait que capturer les
    octets sans les décoder."""
    Gtk, _Gdk, _GLib = _require_gtk4()
    from gcm_gtk4_clipboard_bridge import _dib_to_texture

    outcome = {}

    def on_activate(app):
        dib = _make_indexed_bmp_dib(width=4, height=2)
        texture = _dib_to_texture(dib)
        outcome["texture"] = texture
        app.quit()

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.bmp")
    app.connect("activate", on_activate)
    app.run(None)

    texture = outcome.get("texture")
    assert texture is not None, "le vrai GdkPixbuf n'a pas su décoder le DIB indexé 4bpp"
    assert (texture.get_width(), texture.get_height()) == (4, 2)


def test_clipboard_bridge_files_roundtrip_via_external_client(gtk4_live_enabled, tmp_path):
    """Presse-papier fichiers, sens local -> RDP, posé par un client X11
    EXTERNE réel (xclip -t text/uri-list) — pas seulement en interne.
    Complète les tests texte/image ci-dessus : reste, jusqu'ici, le seul
    des trois types de contenu presse-papier jamais exercé en conditions
    réelles côté GTK4 (cf. features.md). Le sens RDP -> local (téléchargement
    asynchrone via asyncio.ensure_future dans _download_and_set_files) est
    couvert séparément par test_clipboard_bridge_files_download_from_rdp_via_
    external_reader ci-dessous, qui intègre gbulb pour ce seul test — voir
    CLAUDE.md."""
    Gtk, Gdk, GLib = _require_gtk4()
    from gcm_gtk4_clipboard_bridge import ClipboardBridge

    # Fichiers réels sur disque, avec un contenu distinct chacun, pour
    # pouvoir vérifier après coup que rien n'a été tronqué ni mélangé.
    file_a = tmp_path / "rapport.txt"
    file_a.write_text("contenu réel du fichier A — accents éàï")
    file_b = tmp_path / "sous-dossier" / "notes.bin"
    file_b.parent.mkdir()
    file_b.write_bytes(bytes(range(256)))

    # RFC 2483 : une URI par ligne, terminée par CRLF.
    uri_list = "".join(f"file://{p}\r\n" for p in (file_a, file_b)).encode("utf-8")

    results = {}

    def on_activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.present()
        rdp = types.SimpleNamespace(clipboard=types.SimpleNamespace())
        # Le pont n'est utile ici que pour son effet de bord (connexion
        # au signal "changed" du presse-papier dans __init__) — aucune
        # méthode n'est appelée dessus directement, contrairement aux
        # tests texte/image ci-dessus.
        _bridge = ClipboardBridge(rdp, Gdk.Display.get_default())

        captured = {}
        rdp.clipboard.announce_local_files = lambda paths: captured.setdefault("paths", paths)

        def after_write(ret, out, err):
            tick_count = 0

            def poll_capture():
                nonlocal tick_count
                tick_count += 1
                if "paths" in captured or tick_count > 50:
                    results["paths"] = captured.get("paths")
                    app.quit()
                    return False
                return True

            GLib.timeout_add(100, poll_capture)

        _run_async(GLib, ["xclip", "-selection", "clipboard", "-t", "text/uri-list"], after_write, input_bytes=uri_list)

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.files")
    app.connect("activate", on_activate)
    app.run(None)

    got_paths = results.get("paths")
    assert got_paths is not None, "announce_local_files n'a jamais été appelé (voir logs GTK ci-dessus)"
    assert set(got_paths) == {str(file_a), str(file_b)}

    # Le pont ne fait que relayer les chemins : les fichiers eux-mêmes ne
    # doivent pas avoir bougé ni changé de contenu.
    assert file_a.read_text() == "contenu réel du fichier A — accents éàï"
    assert file_b.read_bytes() == bytes(range(256))


def test_clipboard_bridge_files_download_from_rdp_via_external_reader(gtk4_live_enabled):
    """Presse-papier fichiers, sens RDP -> local cette fois (téléchargement
    asynchrone dans `_download_and_set_files`) — jusqu'ici seule la
    logique pure était couverte (tests/test_gtk4_clipboard_file_download.py,
    2026-09-05), faute d'une vraie boucle asyncio intégrée à la boucle
    GLib (`_on_remote_files_changed` appelle un `asyncio.ensure_future()`
    nu, qui ne s'exécute jamais sous un simple `Gtk.Application.run(None)`
    comme le reste de ce fichier). Ce test installe `gbulb` juste pour sa
    propre durée (politique de boucle asyncio restaurée dans un `finally`,
    même discipline que les substitutions `sys.modules` ailleurs dans le
    projet) puis pilote l'application via `loop.run_forever(application=app)`
    plutôt que `app.run(None)`, pour que la boucle GLib de GTK4 SOIT la
    boucle asyncio — vérifié isolément avant application ici (voir
    CLAUDE.md, "Pont fichiers RDP -> local en conditions réelles").

    Sélection à trois entrées (dossier de premier niveau + fichier isolé
    + fichier imbriqué dans le dossier, plage d'octets 0-255 complète
    pour détecter une troncature) — a mis en évidence un vrai bug (voir
    CLAUDE.md) : un dossier de premier niveau n'apparaissait jamais dans
    la Gdk.FileList locale (seul son contenu était recréé sur disque),
    corrigé dans gcm_gtk4_clipboard_bridge.py."""
    Gtk, Gdk, GLib = _require_gtk4()
    import asyncio

    from gcm_gtk4_asyncio import run_application
    from gcm_gtk4_clipboard_bridge import ClipboardBridge

    from asyncrdp import RemoteFileInfo

    remote_contents = {
        0: b"contenu reel du fichier de premier niveau, transport ascii",
        2: bytes(range(256)),
    }
    files = [
        RemoteFileInfo(index=0, name="rapport.txt", size=len(remote_contents[0]), is_directory=False),
        RemoteFileInfo(index=1, name="sous-dossier", size=0, is_directory=True),
        RemoteFileInfo(index=2, name="sous-dossier\\notes.bin", size=len(remote_contents[2]), is_directory=False),
    ]
    requested_indices = []

    async def fake_request_remote_file_contents(index):
        requested_indices.append(index)
        await asyncio.sleep(0)  # force un vrai aller-retour asynchrone, pas une valeur immédiate
        return remote_contents[index]

    results = {}

    def on_activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.present()
        rdp = types.SimpleNamespace(
            clipboard=types.SimpleNamespace(
                request_remote_file_contents=fake_request_remote_file_contents,
            )
        )
        bridge = ClipboardBridge(rdp, Gdk.Display.get_default())
        results["download_dir"] = bridge._download_dir

        bridge._on_remote_files_changed(files)

        poll_tick_count = 0

        def poll_clipboard_ready():
            nonlocal poll_tick_count
            poll_tick_count += 1
            formats = Gdk.Display.get_default().get_clipboard().get_formats()
            if formats.contain_gtype(Gdk.FileList):

                def after_read(ret, out, err):
                    results["uri_list"] = out
                    app.quit()

                _run_async(GLib, ["xclip", "-o", "-selection", "clipboard", "-t", "text/uri-list"], after_read)
                return False
            if poll_tick_count > 100:  # ~5s à 50ms/tick
                results["timed_out"] = True
                app.quit()
                return False
            return True

        GLib.timeout_add(50, poll_clipboard_ready)

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.files.download")
    app.connect("activate", on_activate)

    run_application(app)

    assert not results.get("timed_out"), "le téléchargement/dépôt presse-papier n'a jamais abouti"

    # Le dossier (index 1) n'est jamais demandé au serveur — seul son
    # contenu l'est. Même vérification que la logique pure, en conditions
    # réelles cette fois.
    assert set(requested_indices) == {0, 2}

    download_dir = results["download_dir"]
    assert (download_dir / "rapport.txt").read_bytes() == remote_contents[0]
    assert (download_dir / "sous-dossier" / "notes.bin").read_bytes() == remote_contents[2]

    uri_list = results.get("uri_list", b"").decode("utf-8")
    got_paths = {line[len("file://") :] for line in uri_list.replace("\r\n", "\n").split("\n") if line}
    # Les deux entrées de premier niveau seulement : le fichier isolé ET
    # le dossier lui-même (pas le fichier imbriqué, déjà représenté par
    # son dossier racine) — c'est précisément l'assertion qui échouait
    # avant le correctif (seul rapport.txt apparaissait).
    assert got_paths == {str(download_dir / "rapport.txt"), str(download_dir / "sous-dossier")}


def test_clipboard_image_available_to_external_reader(gtk4_live_enabled, native_clipboard_provider_available):
    """RDP -> local (image), vérifié par un client X11 EXTERNE (xclip) —
    pas seulement en interne via _dib_to_texture (voir
    test_clipboard_bridge_decodes_real_indexed_bmp pour cette partie, qui
    elle passait déjà).

    Constat initial du 2026-09-02 (affiné le même jour, puis le 2026-09-07,
    2026-09-11 x2) : Gdk.ContentProvider.new_for_value() ne déclare aucun
    mime-type sérialisable pour un GdkTexture sur ce GTK4 4.14.5, quelle
    que soit la méthode de construction de la texture — et toute sous-
    classe *Python* de Gdk.ContentProvider qui tente de corriger ça reçoit
    un `user_data` déjà perdu dans do_write_mime_type_async(), avant même
    d'être appelée (marshaling PyGObject de ce vfunc précis — voir
    docs/pygobject-async-vfunc-userdata-bug-report.md). Confirmé
    définitivement hors de portée d'un correctif Python pur le 2026-09-11
    (docs/sessions/session-24.md).

    Résolu le 2026-09-14 (docs/sessions/session-27.md) par une sous-classe
    de Gdk.ContentProvider écrite en C pur (integrations/gtk4/native/,
    exposée via GObject Introspection — voir build_gir.py) : jamais
    surchargée côté Python, elle échappe entièrement à ce marshaling.
    ClipboardBridge._on_remote_image_changed s'en sert désormais quand
    elle est disponible (repli sur l'ancien comportement sinon — voir
    _load_native_image_provider dans gcm_gtk4_clipboard_bridge.py). Le
    mime-type offert est image/png (pas image/bmp comme le demandait la
    version précédente de ce test) : gdk_texture_save_to_png_bytes() est
    l'API GTK4 la plus directe pour encoder une fois, en C, à la
    construction du provider — et image/png est le format que la plupart
    des lecteurs externes réels demandent de toute façon."""
    Gtk, Gdk, GLib = _require_gtk4()
    from gcm_gtk4_clipboard_bridge import ClipboardBridge

    results = {}

    def on_activate(app):
        win = Gtk.ApplicationWindow(application=app)
        win.present()
        rdp = types.SimpleNamespace(clipboard=types.SimpleNamespace())
        bridge = ClipboardBridge(rdp, Gdk.Display.get_default())
        dib = _make_indexed_bmp_dib(width=4, height=2)
        bridge._on_remote_image_changed(dib)

        def after_read(ret, out, err):
            results["bytes"] = out
            app.quit()

        GLib.timeout_add(
            300,
            lambda: (
                _run_async(GLib, ["xclip", "-o", "-selection", "clipboard", "-t", "image/png"], after_read),
                False,
            )[1],
        )

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.image")
    app.connect("activate", on_activate)
    app.run(None)

    assert results.get("bytes", b"")[:8] == b"\x89PNG\r\n\x1a\n", (
        "l'image devrait être servie en PNG à un lecteur externe (provider natif C)"
    )


@pytest.mark.xfail(
    reason=(
        "Tentative de correctif du 2026-09-02 (nouvelle session) pour le "
        "test ci-dessus : un GdkContentProvider custom (a la Vim, "
        "gui_gtk4_cb.c) qui encode la texture en PNG lui-meme "
        "(Gdk.Texture.save_to_png_bytes()) et implemente ses propres "
        "do_ref_formats/do_get_value/do_write_mime_type_async/"
        "do_write_mime_type_finish resout BIEN la premiere moitie du "
        "probleme : ref_formats() et clipboard.get_formats() rapportent "
        "correctement ['image/png'] (contre [] avec Gdk.ContentProvider."
        "new_for_value(), voir test_clipboard_image_available_to_external_"
        "reader ci-dessus). Mais l'ecriture effective echoue avec "
        "'GLib-GIO-CRITICAL: g_task_return_boolean: assertion "
        "G_IS_TASK (task) failed' + 'g_object_unref: assertion "
        "G_IS_OBJECT (object) failed' dans do_write_mime_type_async, "
        "reproduit A L'IDENTIQUE avec 3 strategies d'ecriture differentes "
        "(write_bytes_async imbrique dans un GTask separe, ecriture "
        "synchrone stream.write_bytes(), et ecriture synchrone + "
        "reference Python forte explicite maintenue sur (self, task, "
        "stream) pendant l'operation pour ecarter une collecte prematuree "
        "cote Python) -- les trois echouent pareil, ce qui ecarte a la "
        "fois la strategie d'ecriture et une hypothese de durete de vie "
        "cote Python comme cause. Piste non elucidee, notee pour la "
        "suite dans CLAUDE.md : possible limitation de la marshaling "
        "PyGObject pour Gio.Task.new()/task.return_boolean() quand "
        "source_object est une sous-classe Python d'un type GObject "
        "abstrait (ici Gdk.ContentProvider) plutot qu'un GObject natif. "
        "Resultat cote lecteur externe : toujours 0 octet recu (TIMEOUT), "
        "donc pas de regression a craindre si ce test se met a xpass -- "
        "ce serait au contraire une vraie confirmation qu'un correctif a "
        "ete trouve, d'ou xfail (pas skip). "
        "Mise a jour 2026-09-07 (session suivante) : les deux hypotheses "
        "restantes de la fois precedente (source_object=None au lieu de "
        "self ; reference Python forte explicite sur le provider "
        "lui-meme) ont ete testees, meme echec identique. Test decisif "
        "supplementaire : remplacer entierement Gio.Task par "
        "Gio.SimpleAsyncResult (API depreciee, ne touche jamais a "
        "Gio.Task) dans do_write_mime_type_async/_finish -- l'echec "
        "reste EXACTEMENT le meme message 'g_task_return_boolean: "
        "assertion G_IS_TASK (task) failed', alors que ce code "
        "n'appelle jamais Gio.Task. Des prints dans les 4 methodes du "
        "ContentProvider confirment qu'elles s'executent toutes "
        "integralement et sans erreur (do_write_mime_type_finish "
        "compris) AVANT que l'assertion n'apparaisse. Conclusion "
        "revisee : le bug n'est tres probablement pas dans ce fichier "
        "ni dans notre sous-classe PyGObject de Gdk.ContentProvider, "
        "mais dans le code interne de GDK (backend X11 du presse-papier, "
        "gdkclipboard-x11.c cote sources GTK, non consulte -- "
        "gitlab.gnome.org hors de portee reseau ici) qui enveloppe notre "
        "write_mime_type_async/_finish dans son propre GTask interne. "
        "Hors de portee de ce projet : necessiterait un GTK4 plus recent "
        "que le 4.14.5 Ubuntu empaquete ici, ou une lecture directe des "
        "sources GDK. Voir CLAUDE.md, section du 2026-09-07. "
        "Resolu differemment le 2026-09-14 (session-27.md) : PAS en "
        "corrigeant cette sous-classe Python (impossible, voir le xfail "
        "suivant), mais en l'evitant entierement via une sous-classe C pure "
        "(test_clipboard_image_native_c_provider_delivers_real_png plus "
        "bas, et test_clipboard_image_available_to_external_reader pour la "
        "version integree au pont livre). Ce test-ci reste xfail a raison : "
        "il documente toujours fidelement que la voie PyGObject specifiquement "
        "est sans issue."
    ),
    strict=False,
)
def test_clipboard_image_custom_content_provider_write_bug(gtk4_live_enabled):
    """Variante du test precedent avec un GdkContentProvider custom au
    lieu de Gdk.ContentProvider.new_for_value() -- voir la raison xfail
    ci-dessus pour le detail de ce que ca resout et de ce qui reste
    casse. Ne passe PAS par ClipboardBridge/asyncrdp : ce test est une
    experimentation isolee sur le mecanisme GTK4/GIO lui-meme, pas une
    modification du pont livre."""
    Gtk, Gdk, GLib = _require_gtk4()
    from gi.repository import Gio

    class _PngContentProvider(Gdk.ContentProvider):
        def __init__(self, texture):
            super().__init__()
            self._texture = texture
            self._png_bytes = texture.save_to_png_bytes()

        def do_ref_formats(self):
            return Gdk.ContentFormats.new(["image/png"])

        def do_get_value(self, gvalue):
            gvalue.set_object(self._texture)
            return True

        def do_write_mime_type_async(self, mime_type, stream, io_priority, cancellable, callback, user_data):
            task = Gio.Task.new(self, cancellable, callback, user_data)
            try:
                stream.write_bytes(self._png_bytes, cancellable)
                task.return_boolean(True)
            except GLib.Error as exc:
                task.return_error(exc)

        def do_write_mime_type_finish(self, result):
            return result.propagate_boolean()

    results = {}

    def on_activate(app):
        dib = _make_indexed_bmp_dib(width=4, height=2)
        from gcm_gtk4_clipboard_bridge import _dib_to_texture

        texture = _dib_to_texture(dib)

        win = Gtk.ApplicationWindow(application=app)
        win.present()

        # Formats rapportes AVANT tout envoi au vrai presse-papier :
        # partie du diagnostic qui, elle, reussit deja.
        provider = _PngContentProvider(texture)
        results["formats_direct"] = provider.ref_formats().get_mime_types()

        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(provider)

        def after_read(ret, out, err):
            results["bytes"] = out
            app.quit()

        GLib.timeout_add(
            300,
            lambda: (
                _run_async(GLib, ["xclip", "-o", "-selection", "clipboard", "-t", "image/png"], after_read),
                False,
            )[1],
        )

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.image.customprovider")
    app.connect("activate", on_activate)
    app.run(None)

    assert results.get("formats_direct") == ["image/png"], (
        "partie censee deja marcher (annonce du mime-type) - si ca echoue "
        "aussi, la regression est plus large que documente"
    )
    assert results.get("bytes", b"")[:8] == b"\x89PNG\r\n\x1a\n", (
        "l'image PNG devrait etre livree a un lecteur externe via le ContentProvider custom"
    )


@pytest.mark.xfail(
    reason=(
        "Approfondissement du bug ci-dessus (2026-09-11), avec deux nouvelles "
        "ressources absentes des sessions precedentes : le miroir GitHub "
        "GNOME/gtk (gitlab.gnome.org restait hors de portee reseau, mais "
        "github.com/GNOME/gtk si) et un sandbox avec GTK4/Xvfb reellement "
        "installes ici. Lecture de gdk/gdkclipboard.c (tag 4.14.5) : "
        "gdk_clipboard_write_async() cree un GTask *task* reel et valide, "
        "puis appelle gdk_content_provider_write_mime_type_async(content, "
        "..., gdk_clipboard_write_done, task) -- *task* est donc un pointeur "
        "C authentique au moment ou notre vfunc do_write_mime_type_async() "
        "est invoquee. Teste ici : meme en evitant ENTIEREMENT de faire "
        "transiter le couple (callback, user_data) recu par un second "
        "constructeur GIO (Gio.Task.new(self, cancellable, callback, "
        "user_data), deja exclu comme cause) -- c'est-a-dire en creant un "
        "GTask totalement independant puis en appelant `callback` nous-memes, "
        "a la main, avec le couple recu strictement intact -- le crash "
        "'g_task_return_boolean: assertion G_IS_TASK (task) failed' reste "
        "EXACTEMENT identique, que callback soit appele avec 2 arguments "
        "(laissant gi.CCallback porter son propre user_data interne) ou 3 "
        "(user_data explicite). Fait nouveau et verifie empiriquement : "
        "`user_data` tel que recu par do_write_mime_type_async() vaut deja "
        "None cote Python dans les DEUX cas, alors que le `task` C cote GDK "
        "est reel (cf. lecture de source ci-dessus) -- la perte a donc lieu "
        "AVANT que ce fichier n'y touche, au moment ou PyGObject marshalle "
        "l'appel de ce vfunc precis depuis le C, pas dans la facon dont le "
        "code Python reutilise ensuite ce qu'il a recu. Ecarte donc "
        "definitivement toute piste de correctif ecrite uniquement en Python "
        "dans ce fichier ou dans gcm_gtk4_clipboard_bridge.py, quelle que "
        "soit la technique d'ecriture -- necessiterait soit un correctif "
        "PyGObject/gobject-introspection sur le marshaling du closure "
        "(callback, user_data) de ce vfunc precis, soit une sous-classe "
        "GdkContentProvider ecrite en C (hors de portee d'un pont Python). "
        "Voir docs/sessions/session-24.md pour le detail complet, y compris "
        "les extraits de gdk/gdkclipboard.c, gdk/x11/gdkclipboard-x11.c et "
        "gdk/x11/gdkselectionoutputstream-x11.c consultes. Rapport de bug "
        "PyGObject pret a deposer, avec repro minimale independante de ce "
        "projet : docs/pygobject-async-vfunc-userdata-bug-report.md "
        "(2026-09-11). "
        "Confirmation directe le 2026-09-14 (session-27.md) : une sous-classe "
        "GdkContentProvider ecrite entierement en C (jamais un override "
        "Python, donc jamais marshalee par PyGObject pour CET appel precis) "
        "livre bien un vrai PNG a un lecteur externe des la premiere tentative "
        "-- voir test_clipboard_image_native_c_provider_delivers_real_png plus "
        "bas et integrations/gtk4/native/. Ferme ce point pour de bon : la "
        "cause etait bien le marshaling PyGObject de ce vfunc, pas autre "
        "chose de plus profond cote GDK."
    ),
    strict=False,
)
def test_clipboard_image_write_bug_survives_direct_callback_invocation(gtk4_live_enabled):
    """Variante diagnostique du test ci-dessus : do_write_mime_type_async
    n'utilise JAMAIS Gio.Task.new(self, cancellable, callback, user_data)
    -- elle cree un GTask minimal totalement independant, puis appelle
    `callback` elle-meme, a la main, avec le couple (callback, user_data)
    recu intact. But : prouver que user_data est deja invalide AVANT que
    ce fichier n'y touche, donc hors de portee d'un correctif cote Python
    pur, peu importe la technique employee ici."""
    Gtk, Gdk, GLib = _require_gtk4()
    from gi.repository import Gio

    class _DirectCallbackContentProvider(Gdk.ContentProvider):
        def __init__(self, texture):
            super().__init__()
            self._texture = texture
            self._png_bytes = texture.save_to_png_bytes()

        def do_ref_formats(self):
            return Gdk.ContentFormats.new(["image/png"])

        def do_get_value(self, gvalue):
            gvalue.set_object(self._texture)
            return True

        def do_write_mime_type_async(self, mime_type, stream, io_priority, cancellable, callback, user_data):
            # Jamais transiter (callback, user_data) par un second
            # constructeur GIO : ce GTask-ci est independant, jamais lie
            # au couple recu.
            self._last_ok, self._last_error = True, None
            try:
                stream.write_bytes(self._png_bytes, cancellable)
            except GLib.Error as exc:
                self._last_ok, self._last_error = False, exc
            inner_task = Gio.Task.new(self, cancellable, None, None)
            if self._last_ok:
                inner_task.return_boolean(True)
            else:
                inner_task.return_error(self._last_error)
            # Appel manuel et direct, avec le couple recu strictement
            # intact (jamais reinjecte dans un constructeur GIO).
            try:
                callback(self, inner_task)
            except TypeError:
                callback(self, inner_task, user_data)

        def do_write_mime_type_finish(self, result):
            if self._last_ok:
                return True
            raise self._last_error

    results = {}

    def on_activate(app):
        dib = _make_indexed_bmp_dib(width=4, height=2)
        from gcm_gtk4_clipboard_bridge import _dib_to_texture

        texture = _dib_to_texture(dib)

        win = Gtk.ApplicationWindow(application=app)
        win.present()

        provider = _DirectCallbackContentProvider(texture)
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(provider)

        def after_read(ret, out, err):
            results["bytes"] = out
            app.quit()

        GLib.timeout_add(
            300,
            lambda: (
                _run_async(GLib, ["xclip", "-o", "-selection", "clipboard", "-t", "image/png"], after_read),
                False,
            )[1],
        )

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.image.directcallback")
    app.connect("activate", on_activate)
    app.run(None)

    assert results.get("bytes", b"")[:8] == b"\x89PNG\r\n\x1a\n", (
        "l'image PNG devrait etre livree meme en appelant callback "
        "directement, sans jamais faire transiter (callback, user_data) "
        "par un second constructeur GIO"
    )


def test_clipboard_image_native_c_provider_delivers_real_png(gtk4_live_enabled, native_clipboard_provider_available):
    """Suite directe des deux xfail ci-dessus : les trois stratégies
    PyGObject testées (ContentProvider.new_for_value, sous-classe Python
    avec Gio.Task, sous-classe Python avec GTask indépendant + appel
    manuel du callback) échouent toutes à l'identique parce que
    `user_data` est déjà perdu avant même que le code Python de ce
    fichier ne soit invoqué (marshaling PyGObject du vfunc
    do_write_mime_type_async, voir les deux xfail ci-dessus et
    docs/sessions/session-24.md).

    Ce test-ci n'écrit plus AUCUNE ligne de sous-classe Python de
    Gdk.ContentProvider : AsyncrdpClipboard.ImageContentProvider
    (integrations/gtk4/native/asyncrdp-image-provider.c) est un GObject
    natif, compilé et exposé via GObject Introspection (build_gir.py) —
    GDK appelle directement le pointeur de fonction C dans sa vtable,
    sans jamais passer par le mécanisme de marshaling vfunc-Python de
    PyGObject pour cet appel précis. Voir docs/sessions/session-27.md
    pour le détail complet de la vérification."""
    Gtk, Gdk, GLib = _require_gtk4()
    import gi

    # Importer le pont d'abord : c'est lui qui ajoute native/_build au chemin
    # de recherche des typelibs (_load_native_image_provider).
    from gcm_gtk4_clipboard_bridge import _dib_to_texture

    gi.require_version("AsyncrdpClipboard", "1.0")
    from gi.repository import AsyncrdpClipboard

    results = {}

    def on_activate(app):
        dib = _make_indexed_bmp_dib(width=4, height=2)
        texture = _dib_to_texture(dib)

        win = Gtk.ApplicationWindow(application=app)
        win.present()

        provider = AsyncrdpClipboard.ImageContentProvider.new(texture)
        results["formats_direct"] = provider.ref_formats().get_mime_types()

        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set_content(provider)

        def after_read(ret, out, err):
            results["bytes"] = out
            app.quit()

        GLib.timeout_add(
            300,
            lambda: (
                _run_async(GLib, ["xclip", "-o", "-selection", "clipboard", "-t", "image/png"], after_read),
                False,
            )[1],
        )

    app = Gtk.Application(application_id="org.asyncrdp.test.clip.image.nativeprovider")
    app.connect("activate", on_activate)
    app.run(None)

    assert results.get("formats_direct") == ["image/png"]
    assert results.get("bytes", b"")[:8] == b"\x89PNG\r\n\x1a\n", (
        "le provider natif C devrait livrer un vrai PNG à un lecteur externe, "
        "là où les trois tentatives PyGObject échouaient toutes"
    )


def test_demo_session_assembles_real_window_display_and_clipboard_bridge(gtk4_live_enabled):
    """Assemble RdpView + ClipboardBridge — les VRAIS modules, pas les
    doublures de tests/gtk_stub/ — dans une VRAIE Gtk.ApplicationWindow
    sous un VRAI Gdk.Display (Xvfb), via RdpSession
    (gcm_gtk4_rdp_session.py, 2026-09-18). C'est le candidat resté ouvert
    dans CLAUDE.md (« Consolider le plugin RDP de GCM ») : jusqu'ici les
    deux ponts étaient validés pièce par pièce (voir les autres tests de
    ce fichier) mais jamais réunis dans une seule application.

    Seule la couche protocole RDP est simulée ici (_FakeConnectFn, sans
    réseau ni FreeRDP) — elle est déjà validée en conditions réelles par
    ailleurs (test_integration_live.py, 108 tests passants au 2026-09-16,
    voir CLAUDE.md). Le sujet précis de CE test est l'assemblage GTK4
    lui-même : RdpSession.connect() construit-il un vrai RdpView attaché +
    un vrai ClipboardBridge câblé sur un vrai Gdk.Clipboard, et
    RdpSession.disconnect() défait-il proprement les deux à la fermeture
    de la fenêtre, sans laisser de tâche en arrière-plan ni de connexion
    ouverte.

    Écrit le 2026-09-18 (session 31), PAS exécuté avec succès cette
    session-là — le Xvfb disponible n'avait aucun module GLX
    (`Gdk.Display.open()` y renvoyait silencieusement None), ce qui
    bloquait toute fenêtre GTK4 réelle indépendamment de ce test précis.

    **Résolu le 2026-09-18 (session 32, nouveau bac à sable)** : ce Xvfb-
    ci expose bel et bien GLX (`xdpyinfo -queryExtensions` le liste,
    contrairement au précédent — confirme que c'est bien une variation
    d'un bac à sable à l'autre, pas une limitation permanente de ce type
    d'environnement, voir le paragraphe dédié en tête de fichier).
    `Gdk.Display.get_default()` fonctionne réellement, et ce test PASSE,
    seul comme dans la suite complète du fichier. A révélé au passage un
    second bug, réel celui-ci et indépendant de GLX : lancé après
    `test_display_bridge_renders_correct_colors_onscreen` dans le même
    process, `RuntimeError: could not create new GType: RdpView` — le
    helper `_import_display_and_session_with_stub_asyncrdp()` rejouait
    inconditionnellement la définition de `class RdpView(Gtk.Widget)`,
    qui enregistre un GType global au process, impossible à
    réenregistrer sous le même nom même après restauration de
    sys.modules. Corrigé dans ce helper (voir son commentaire dédié) :
    réutilise le module déjà chargé si présent, le laisse résident sinon
    — plus de second enregistrement, quel que soit l'ordre des tests.
    Suite `test_gtk4_live.py` complète rejouée après correctif : 8
    passed, 2 xfailed (les deux bugs PyGObject connus, sans lien), 0
    failed. Suite complète du projet (hors test_integration_live.py,
    qui nécessite un vrai serveur RDP, non monté cette session) : 122
    passed, 1 skipped, 2 xfailed, 0 failed — aucune régression. Voir
    `docs/sessions/session-32.md`."""
    Gtk, Gdk, GLib = _require_gtk4()
    import asyncio

    from gcm_gtk4_asyncio import run_application

    RdpView, RdpSession = _import_display_and_session_with_stub_asyncrdp()

    class _FakeRdpClient:
        """Juste assez pour RdpView.attach() (frame_size/get_frame) et
        ClipboardBridge.__init__ (assigne des callables sur .clipboard) —
        seule la couche protocole RDP est simulée ; GTK4, Gdk.Display,
        RdpView et ClipboardBridge sont tous réels."""

        def __init__(self):
            self.frame_size = (64, 48)
            self.clipboard = types.SimpleNamespace()

        async def get_frame(self):
            # Ne se résout jamais avant d'être annulée par
            # RdpView.detach() (cf. _pump_frames) : aucun vrai flux vidéo
            # à simuler pour ce test, seul l'assemblage importe ici.
            await asyncio.sleep(3600)
            raise AssertionError("get_frame() n'aurait jamais dû se résoudre dans ce test")

    class _FakeConnectFn:
        """Doublure de asyncrdp.connect (même contrat : un appel renvoie
        un gestionnaire de contexte asynchrone cédant un client) — sans
        réseau ni FreeRDP, pour isoler l'assemblage GTK4 du protocole."""

        def __init__(self):
            self.client = _FakeRdpClient()
            self.exited = False

        def __call__(self, host, port, username, password, *, domain=None, options=None):
            return self

        async def __aenter__(self):
            return self.client

        async def __aexit__(self, *exc_info):
            self.exited = True
            return False

    fake_connect = _FakeConnectFn()
    results = {}

    def on_activate(app):
        window = Gtk.ApplicationWindow(application=app)
        view = RdpView()
        window.set_child(view)
        window.present()

        session = RdpSession(view=view, gdk_display=Gdk.Display.get_default(), connect_fn=fake_connect)

        async def run():
            try:
                client = await session.connect("fake-host", 3389, "u", "p")
                results["attached"] = view._rdp is client
                results["clipboard_wired"] = client.clipboard.on_remote_text_changed is not None

                await session.disconnect()
                results["detached"] = view._rdp is None
                results["clipboard_unwired"] = client.clipboard.on_remote_text_changed is None
                results["connection_closed"] = fake_connect.exited
            finally:
                app.quit()

        # Référence gardée dans `results` (vivant pour toute la durée du
        # test) plutôt qu'un asyncio.ensure_future() nu : même risque de
        # garbage-collection en plein vol que celui déjà trouvé et corrigé
        # ailleurs dans ce projet le 2026-09-09 (voir CLAUDE.md), signalé
        # ici par ruff (RUF006) avant même d'avoir été exécuté.
        results["_task"] = asyncio.ensure_future(run())

        def _timeout_guard():
            if "connection_closed" not in results:
                results["timed_out"] = True
                app.quit()
            return False

        GLib.timeout_add(10000, _timeout_guard)

    app = Gtk.Application(application_id="org.asyncrdp.test.demo.viewer.assembly")
    app.connect("activate", on_activate)

    run_application(app)

    assert not results.get("timed_out"), "l'assemblage/désassemblage n'a jamais abouti"
    assert results.get("attached") is True, "RdpView.attach() aurait dû recevoir le client de la session"
    assert results.get("clipboard_wired") is True, "ClipboardBridge aurait dû câbler les callbacks du client"
    assert results.get("detached") is True, "RdpView.detach() aurait dû être appelée à la déconnexion"
    assert results.get("clipboard_unwired") is True, "ClipboardBridge.close() aurait dû désabonner les callbacks"
    assert results.get("connection_closed") is True, "RdpSession.disconnect() aurait dû refermer la connexion"
