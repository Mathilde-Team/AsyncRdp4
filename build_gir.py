# SPDX-License-Identifier: GPL-3.0-or-later
"""
build_gir.py — build de integrations/gtk4/native/, une petite bibliothèque
GObject Introspection écrite en C pur, distincte du binding cffi principal
(build_ffi.py, qui ne concerne que libfreerdp3).

Contient un seul type pour l'instant : AsyncrdpImageContentProvider, une
sous-classe C de Gdk.ContentProvider pour contourner le bug de marshaling
PyGObject documenté dans CLAUDE.md / docs/features-backlog.md (section
presse-papier image) — voir asyncrdp-image-provider.h pour le détail complet
du raisonnement. Une sous-classe *Python* de Gdk.ContentProvider ne peut pas
contourner ce bug puisque le problème est justement dans le marshaling
PyGObject de l'override vfunc lui-même ; seul du code C pur, jamais appelé
via ce mécanisme, y échappe.

Prérequis système (Debian/Ubuntu) :
    apt install libgtk-4-dev gobject-introspection libgirepository1.0-dev

Utilisation :
    python3 build_gir.py
        -> integrations/gtk4/native/_build/libasyncrdp-clipboard-1.0.so
           integrations/gtk4/native/_build/AsyncrdpClipboard-1.0.gir
           integrations/gtk4/native/_build/AsyncrdpClipboard-1.0.typelib

gcm_gtk4_clipboard_bridge.py ajoute automatiquement ce dossier _build/ à
GI_TYPELIB_PATH avant d'importer gi.repository.AsyncrdpClipboard — rien à
configurer manuellement une fois ce script exécuté. Si ce script n'a pas
été exécuté (typelib absent), le pont continue de fonctionner en repli sur
Gdk.ContentProvider.new_for_value() (même limitation qu'avant : mime-types
vides pour un lecteur externe), avec un avertissement loggé une seule fois
plutôt qu'un ImportError qui casserait toute la classe ClipboardBridge.
"""

import os
import subprocess
import sys

from loguru import logger  # noqa: F401 — logger de module exigé (SOCLE §8)

from asyncrdp.tracing import traced

_HERE = os.path.dirname(os.path.abspath(__file__))
_NATIVE_DIR = os.path.join(_HERE, "integrations", "gtk4", "native")
_BUILD_DIR = os.path.join(_NATIVE_DIR, "_build")

_NAMESPACE = "AsyncrdpClipboard"
_NSVERSION = "1.0"
_LIBNAME = "asyncrdp-clipboard-1.0"  # -> libasyncrdp-clipboard-1.0.so
_SOURCE = os.path.join(_NATIVE_DIR, "asyncrdp-image-provider.c")
_HEADER = os.path.join(_NATIVE_DIR, "asyncrdp-image-provider.h")


def _pkg_config(flag):
    # Une seule dépendance pkg-config (gtk4) : elle tire déjà glib/gobject/
    # gio/gdk-pixbuf/cairo en transitif, comme dans le reste du projet.
    return subprocess.run(["pkg-config", flag, "gtk4"], capture_output=True, text=True, check=True).stdout.split()


@traced
def main():
    for src in (_SOURCE, _HEADER):
        if not os.path.isfile(src):
            print(f"erreur : {src} introuvable", file=sys.stderr)
            return 1

    os.makedirs(_BUILD_DIR, exist_ok=True)
    so_path = os.path.join(_BUILD_DIR, f"lib{_LIBNAME}.so")
    gir_path = os.path.join(_BUILD_DIR, f"{_NAMESPACE}-{_NSVERSION}.gir")
    typelib_path = os.path.join(_BUILD_DIR, f"{_NAMESPACE}-{_NSVERSION}.typelib")

    cflags = _pkg_config("--cflags")
    libs = _pkg_config("--libs")

    print("== 1. Compilation de la bibliothèque partagée ==")
    subprocess.run(
        ["gcc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", *cflags, _SOURCE, "-o", so_path, *libs],
        check=True,
    )

    print("== 2. Scan GObject Introspection (.gir) ==")
    # --pkg=gtk4 plutôt que de repasser les cflags bruts de _pkg_config()
    # ci-dessus : g-ir-scanner a son propre analyseur d'arguments (optparse),
    # qui interprète à tort des flags gcc comme -mfpmath=sse/-msse2 comme des
    # options courtes -m qui lui sont destinées (« no such option: -m »).
    # --pkg délègue l'appel à pkg-config en interne, sans ce conflit.
    subprocess.run(
        [
            "g-ir-scanner",
            f"--namespace={_NAMESPACE}",
            f"--nsversion={_NSVERSION}",
            "--include=Gtk-4.0",
            "--pkg=gtk4",
            # Préfixe C réel des symboles (Asyncrdp/asyncrdp_), distinct du nom
            # de namespace GI (AsyncrdpClipboard) — sans ceci, g-ir-scanner
            # rejette silencieusement tous les symboles comme hors namespace
            # (« Unknown namespace for symbol ») et le .gir sort vide.
            "--identifier-prefix=Asyncrdp",
            "--symbol-prefix=asyncrdp",
            "--library-path",
            _BUILD_DIR,
            f"--library={_LIBNAME}",
            "--no-libtool",
            "-o",
            gir_path,
            _HEADER,
            _SOURCE,
        ],
        check=True,
        cwd=_NATIVE_DIR,
    )

    print("== 3. Chemin absolu de la bibliothèque partagée dans le .gir ==")
    # g-ir-scanner n'écrit que le nom nu (libasyncrdp-clipboard-1.0.so) dans
    # l'attribut shared-library, résolu ensuite par le lieur dynamique via
    # son chemin de recherche standard — jamais garanti pour une
    # bibliothèque qui n'est pas installée à un emplacement système (le cas
    # ici, _build/ n'est pas censé être ajouté à /etc/ld.so.conf). Remplacer
    # par un chemin absolu rend le typelib utilisable sans LD_LIBRARY_PATH.
    with open(gir_path, encoding="utf-8") as f:
        gir_xml = f.read()
    needle = f'shared-library="lib{_LIBNAME}.so"'
    if needle not in gir_xml:
        print(f"erreur : attribut shared-library attendu introuvable dans {gir_path}", file=sys.stderr)
        return 1
    gir_xml = gir_xml.replace(needle, f'shared-library="{so_path}"')
    with open(gir_path, "w", encoding="utf-8") as f:
        f.write(gir_xml)

    print("== 4. Compilation du .typelib ==")
    subprocess.run(["g-ir-compiler", gir_path, "-o", typelib_path], check=True)

    print(f"OK :\n  {so_path}\n  {gir_path}\n  {typelib_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
