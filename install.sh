#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# install.sh — installe les dépendances système puis le package asyncrdp
# (via uv, cf. migration du 2026-09-15 — voir docs/sessions/session-28.md).
#
# Usage :
#   git clone https://github.com/<votre-compte>/asyncrdp.git
#   cd asyncrdp
#   ./install.sh              # installation standard
#   ./install.sh --dev        # + dépendances de test (pytest)
#   ./install.sh --gtk4       # + PyGObject (ponts integrations/gtk4/)
#   ./install.sh --usb        # + pyusb/libusb + règle udev (énumération et
#                              #   accès non-root, voir asyncrdp.usb)
#   ./install.sh --dev --gtk4
#
# Installe le package au niveau système (voir note plus bas), pas dans un
# environnement virtuel — pour un venv isolé + outillage dev (ruff, pytest)
# via un fichier de verrouillage, voir `uv sync` (nouveau, remplace
# l'ancien flux pip-only ; CLAUDE.md, section « Commandes de qualité »).
#
# Testé contre Ubuntu 24.04 + FreeRDP 3.30.0. Si votre distribution n'a
# que FreeRDP2 (libfreerdp2-dev), voir la note en bas de ce script.

set -euo pipefail

if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
else
    SUDO=""
fi

DEV=0
GTK4=0
USB=0
PYGOBJECT_CONSTRAINT=()  # rempli plus bas si --gtk4 (voir ce bloc pour le pourquoi)
for arg in "$@"; do
    case "$arg" in
        --dev) DEV=1 ;;
        --gtk4) GTK4=1 ;;
        --usb) USB=1 ;;
        *) echo "Option inconnue : $arg" >&2; exit 1 ;;
    esac
done

echo "== 1. Dépendances système =="
# apt update peut renvoyer un code d'erreur si UN SEUL dépôt configuré est
# injoignable (dépôt tiers, proxy d'entreprise, etc.) même si les dépôts
# dont on a besoin ont bien été rafraîchis — on ne bloque pas le script
# pour ça, seul l'échec de l'install qui suit est réellement bloquant.
$SUDO apt update || echo "avertissement : apt update a rencontré une erreur sur au moins un dépôt, on continue"
$SUDO apt install -y \
    build-essential pkg-config \
    freerdp3-dev libwinpr3-dev \
    python3-dev python3-pip

if [ "$GTK4" -eq 1 ]; then
    $SUDO apt install -y python3-gi gir1.2-gtk-4.0

    # PyGObject récent (>=3.52) exige girepository-2.0 au lien -- absent
    # des dépôts Ubuntu 24.04 (seul girepository-1.0 y est empaqueté, via
    # libgirepository1.0-dev installé plus bas). `uv pip install`, à
    # l'étape 3, ne voit pas le python3-gi apt tout juste installé
    # ci-dessus (pas de métadonnées pip pour un paquet dpkg) et tente
    # donc par défaut d'installer la toute dernière version PyPI, dont la
    # compilation échoue à l'étape meson
    # (« Dependency 'girepository-2.0' is required but not found »).
    # Trouvé le 2026-09-18 (session 32) : ce script échouait réellement
    # avec --gtk4 sur une Ubuntu 24.04 neuve avant ce correctif -- pas
    # une hypothèse. Contournement : contraindre uv à la même version que
    # celle qu'apt vient d'installer, plutôt que de laisser PyPI
    # résoudre la plus récente (ni recompilation contre girepository-2.0,
    # ni divergence entre le PyGObject système et celui que uv installe).
    PYGOBJECT_APT_VERSION=$(python3 -c "import gi; print(gi.__version__)" 2>/dev/null || true)
    if [ -n "$PYGOBJECT_APT_VERSION" ]; then
        PYGOBJECT_CONSTRAINT=("PyGObject==${PYGOBJECT_APT_VERSION}")
    fi

    # Bibliothèque GObject Introspection native (integrations/gtk4/native/,
    # voir build_gir.py et CLAUDE.md, section presse-papier image) : un
    # GdkContentProvider écrit en C pur, seul moyen trouvé de contourner un
    # bug de marshaling PyGObject précis (do_write_mime_type_async() d'une
    # sous-classe Python perd son user_data avant même d'être appelée).
    # Dépendances de compilation seulement (pas requises pour utiliser un
    # typelib déjà construit) : absentes d'un poste qui ne fait
    # qu'installer le paquet, d'où un bloc séparé plutôt qu'un ajout à la
    # ligne apt juste au-dessus.
    $SUDO apt install -y libgtk-4-dev gobject-introspection libgirepository1.0-dev
    if python3 build_gir.py; then
        echo "Provider presse-papier image natif compilé (integrations/gtk4/native/_build/)."
    else
        echo "avertissement : échec de compilation du provider presse-papier natif -- ClipboardBridge se dégradera proprement vers l'ancien comportement (mime-types vides pour un lecteur externe, voir CLAUDE.md)."
    fi
fi

if [ "$USB" -eq 1 ]; then
    # pyusb (extra pip) s'appuie sur libusb-1.0 au runtime — le paquet
    # système fournit la lib partagée, pyusb ne l'embarque pas.
    $SUDO apt install -y libusb-1.0-0

    # Règle udev nécessaire pour l'accès non-root aux device nodes USB
    # bruts — voir udev/70-asyncrdp-usb.rules pour le détail complet et
    # les compromis. Sans elle : list_usb_devices() ne lit pas les
    # descripteurs de chaîne (fabricant/produit/série restent à None) et
    # toute redirection effective échoue avec LIBUSB_ERROR_ACCESS.
    # Ignoré proprement (avertissement, pas d'échec du script) si udevadm
    # est absent OU présent mais injoignable : un conteneur/CI peut avoir
    # le binaire udevadm (installé comme dépendance d'un autre paquet)
    # sans qu'aucun démon udev ne tourne réellement — `command -v` seul ne
    # détecte pas ce second cas. Repéré le 2026-09-11 (session 26) : dans
    # ce sandbox précis, `udevadm control --reload-rules` échoue avec
    # « Failed to send reload request: No such file or directory » (pas
    # de socket udev), ce qui sous `set -e` faisait avorter tout le script
    # AVANT l'installation du package Python — la règle n'était donc
    # jamais posée alors que rien ne le signalait à l'utilisateur.
    if command -v udevadm >/dev/null 2>&1; then
        $SUDO cp udev/70-asyncrdp-usb.rules /etc/udev/rules.d/
        if $SUDO udevadm control --reload-rules 2>/dev/null && $SUDO udevadm trigger 2>/dev/null; then
            echo "Règle udev installée (/etc/udev/rules.d/70-asyncrdp-usb.rules)."
            echo "Note : un périphérique déjà branché avant l'installation ne récupère l'accès qu'après un débranchement/rebranchement (ou un redémarrage)."
        else
            echo "avertissement : udevadm présent mais injoignable (pas de démon udev actif — conteneur/CI sans udev réel, ou service non démarré)."
            echo "  Règle copiée dans /etc/udev/rules.d/ mais pas rechargée ; elle prendra effet au prochain démarrage d'udev sur une vraie machine."
        fi
    else
        echo "avertissement : udevadm introuvable, règle udev non installée (normal dans un conteneur/CI sans udev réel)."
        echo "  Sur une vraie machine : sudo cp udev/70-asyncrdp-usb.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger"
    fi
fi

echo "== 2. Vérification des symboles sensibles avant compilation =="
# Ces points sont signalés comme à revérifier dans les commentaires du
# shim (src/asyncrdp/_shim.c) — on vérifie ici plutôt que de découvrir un
# échec de link plus tard.
FREERDP_LIB=$(pkg-config --variable=libdir freerdp3 2>/dev/null || echo "/usr/lib/x86_64-linux-gnu")
echo "Symboles freerdp_get_last_error_* disponibles :"
for lib in "$FREERDP_LIB"/libfreerdp3.so*; do
    [ -e "$lib" ] || continue
    nm -D "$lib" 2>/dev/null | grep -o "freerdp_get_last_error_[a-z]*" | sort -u || true
done

echo "== 3. Installation du package (compile le shim C via cffi_modules) =="
# Liste construite dynamiquement plutôt que par énumération de cas fixes
# (l'ancienne version ne couvrait pas toutes les combinaisons dès qu'une
# troisième option comme --usb s'est ajoutée aux deux déjà existantes).
EXTRA_LIST=()
[ "$DEV" -eq 1 ] && EXTRA_LIST+=("test")
[ "$GTK4" -eq 1 ] && EXTRA_LIST+=("gtk4")
[ "$USB" -eq 1 ] && EXTRA_LIST+=("usb")
EXTRAS=""
if [ "${#EXTRA_LIST[@]}" -gt 0 ]; then
    IFS=,
    EXTRAS="[${EXTRA_LIST[*]}]"
    unset IFS
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv introuvable, installation via pip..."
    python3 -m pip install --break-system-packages -q uv
fi

# Ubuntu/Debian récents (PEP 668) refusent pip install système sans ce
# flag. On reste volontairement en install système plutôt qu'en venv,
# cohérent avec le reste de ce script qui installe déjà les dépendances
# via apt au niveau système (notamment python3-gi pour --gtk4, qui
# s'appuie sur les typelibs GObject Introspection système et n'a pas de
# sens dans un venv isolé sans --system-site-packages). `uv pip install`
# est utilisé ici comme remplacement direct, plus rapide, de `pip
# install` — pas le mode « projet » de uv (uv sync/uv.lock), qui reste
# réservé au développement de la bibliothèque cœur (voir CLAUDE.md).
uv pip install --system --break-system-packages -e ".${EXTRAS}" "${PYGOBJECT_CONSTRAINT[@]}"

echo "== 4. Vérification =="
python3 -c "import asyncrdp; print('asyncrdp', asyncrdp.__version__, 'installé et importable')"

echo "== OK =="
echo "Test minimal   : python3 examples/test_connect_minimal.py <host> <user> <password>"
echo "Test protocole : python3 examples/test_connect_full.py <host> <user> <password>"
if [ "$DEV" -eq 1 ]; then
    echo "Tests unitaires : pytest tests/"
    echo "Tests d'intégration (nécessitent un serveur RDP) :"
    echo "  ASYNCRDP_TEST_HOST=<host> ASYNCRDP_TEST_USER=<user> ASYNCRDP_TEST_PASSWORD=<pass> pytest tests/test_integration_live.py -v"
fi

# ---------------------------------------------------------------------
# Note FreeRDP2 vs FreeRDP3 :
# Si seul libfreerdp2-dev est disponible sur votre distribution, l'API de
# settings change fondamentalement (accès direct aux champs de
# rdpSettings plutôt que freerdp_settings_set_*), et freerdp_client_load_addins
# n'existe pas sous ce nom dans toutes les versions 2.x. Adapter
# src/asyncrdp/_shim.c est alors un vrai travail de portage, pas un
# simple ajustement de nom de symbole.
#
# Note H.264/GFX :
# Le paquet freerdp3-dev standard n'a PAS de décodeur H.264 câblé
# (WITH_OPENH264=OFF à la compilation) — activer RdpOptions.enable_h264
# avec cette installation provoquera un crash dès qu'un flux H.264 réel
# arrive. Voir docs/H264_BUILD_GUIDE.md pour recompiler FreeRDP avec
# -DWITH_OPENH264=ON si cette fonctionnalité est nécessaire.
# ---------------------------------------------------------------------
