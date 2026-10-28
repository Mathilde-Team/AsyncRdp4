# asyncrdp

Binding asyncio sur libfreerdp3, pensé comme l'équivalent RDP d'[asyncvnc2](https://github.com/) pour GCM (gnome-connection-manager) — même ergonomie, même intégration asyncio native, pas de sous-processus `xfreerdp` à piloter.

```python
import asyncio
import asyncrdp


async def main():
    async with asyncrdp.connect("192.168.1.10", username="alice", password="secret") as client:
        frame = await client.get_frame()
        print(client.frame_size)


asyncio.run(main())
```

## État du projet

Ce n'est plus un squelette : le cœur de la bibliothèque a été compilé, exécuté et testé contre un vrai serveur RDP (xrdp), avec de vraies données transitant dans les deux sens — pas seulement une relecture de code. L'état courant est dans [`CLAUDE.md`](./CLAUDE.md) ; le détail complet des tests, bugs trouvés/corrigés et limitations connues est dans [`docs/features-backlog.md`](./docs/features-backlog.md) et l'archive de sessions [`docs/sessions/`](./docs/sessions/).

**Résumé de ce qui est validé en conditions réelles :**

| Fonctionnalité | Statut |
|---|---|
| Connexion, TLS/NLA, affichage (GDI/frames) | ✅ testé |
| Clavier, souris | ✅ testé |
| Resize dynamique | ✅ testé |
| Clipboard texte (bidirectionnel) | ✅ testé, round-trip identique |
| Clipboard image (bidirectionnel) | ✅ testé, byte-identique |
| Clipboard fichiers (bidirectionnel, avec dossiers récursifs) | ✅ testé, contenu identique |
| Disque redirigé (lecture + écriture) | ✅ testé, les deux sens |
| Multi-écran | ✅ testé |
| Audio (lecture + capture) | ⚠️ charge utile réelle confirmée dans les deux sens contre un vrai pipeline PipeWire (ton à 440 Hz mesuré exact en capture) ; lecture physique côté client non confirmée, faute de carte ALSA virtuelle en sandbox |
| Pipeline graphique RDPGFX / H.264 | ✅ validé de bout en bout (5 frames réelles reçues en continu) — nécessite de recompiler FreeRDP avec `-DWITH_OPENH264=ON` (le paquet système en est dépourvu). Voir [`H264_BUILD_GUIDE.md`](./H264_BUILD_GUIDE.md) |
| Imprimante, série, parallèle | ❌ confirmé au niveau du code source de xrdp que ce n'est structurellement jamais implémenté — un serveur RDP différent serait nécessaire |
| USB | ⚠️ énumération confirmée sur du vrai matériel (bug de droits trouvé et corrigé) ; redirection de bout en bout non confirmée, faute des droits udev nécessaires — voir [`udev/70-asyncrdp-usb.rules`](./udev/70-asyncrdp-usb.rules) |
| Intégration GTK4 (affichage + ponts clipboard) | ✅ testée en conditions réelles (Xvfb et vrai écran physique), y compris l'offre de l'image au presse-papier système vérifiée par un lecteur X11 externe (nécessite le provider natif, voir ci-dessous) |
| Assemblage GTK4 (`RdpSession` + visualiseur de référence) | ✅ testé en conditions réelles (17 tests de logique pure + `test_demo_session_assembles_real_window_display_and_clipboard_bridge` sous Xvfb+GLX réel, voir `docs/sessions/session-32.md`) |

## Architecture

```
asyncrdp/
├── install.sh                  Installation en une commande (apt + uv), règle udev USB incluse (--usb)
├── pyproject.toml              Métadonnée du package (PEP 621)
├── setup.py                    Minimal — nécessaire pour cffi_modules
├── build_ffi.py                Script de compilation du module cffi
├── build_gir.py                Compile le provider presse-papier natif (GObject Introspection)
├── MANIFEST.in                 Inclut _shim.c, les sources natives et les fichiers non-Python dans la distribution source
├── udev/
│   └── 70-asyncrdp-usb.rules   Accès non-root aux device nodes USB (installée par install.sh --usb)
├── src/asyncrdp/
│   ├── __init__.py             Point d'entrée public (réexporte l'API)
│   ├── _core.py                Bibliothèque cœur — API publique asyncio
│   ├── _shim.c                 Shim C compilé contre libfreerdp3
│   ├── usb.py                  Énumération USB (libusb/pyusb) + diagnostic de permissions
│   └── tracing.py              Décorateur @traced (entrée/sortie tracée via loguru)
├── integrations/gtk4/
│   ├── gcm_gtk4_display_bridge.py     Widget GTK4 (Gtk.Picture + input)
│   ├── gcm_gtk4_clipboard_bridge.py   Pont Gdk.Clipboard <-> asyncrdp.Clipboard
│   ├── gcm_gtk4_rdp_session.py        Assemble connexion + les deux ponts ci-dessus (cycle de vie commun)
│   ├── gcm_gtk4_demo_viewer.py        Visualiseur GTK4 de référence, réellement exécutable
│   └── native/                        GdkContentProvider en C (image du presse-papier, voir build_gir.py)
├── examples/
│   ├── test_connect_minimal.py        Test minimal : connexion + quelques frames
│   ├── test_connect_full.py           Test protocole (autonome)
│   ├── test_full_suite.py             Suite complète, y compris tests dépendants de l'environnement
│   └── probe_rdpdr_devices.py         Sonde : annonce imprimante/série/parallèle + disque témoin
├── tests/
│   ├── test_rdp_options.py            Unitaires, pas de serveur nécessaire
│   ├── test_file_group_descriptor.py  Unitaires, pas de serveur nécessaire
│   ├── test_usb.py                    Unitaires (usb.py) + un appel réel non mocké contre libusb
│   ├── test_tracing.py                Unitaires du décorateur @traced
│   ├── test_no_circular_imports.py    Graphe des imports internes, construit par ast
│   ├── test_gtk4_bridges.py           Ponts GTK4, logique pure via tests/gtk_stub/
│   ├── test_gtk4_demo_viewer.py       RdpSession + CLI du visualiseur, logique pure via tests/gtk_stub/
│   ├── test_gtk4_live.py              Ponts GTK4 (+ assemblage RdpSession) en conditions réelles (opt-in, ASYNCRDP_TEST_GTK4)
│   ├── test_gtk4_clipboard_file_download.py  Pont fichiers RDP→local, logique pure
│   └── test_integration_live.py       Contre un vrai serveur (opt-in, ASYNCRDP_TEST_HOST)
└── docs/
    ├── features-backlog.md            Statut des fonctionnalités, backlog trié par urgence
    ├── architecture-and-lessons.md    Décisions d'architecture et bugs racines
    ├── test-environment.md            Recette pour remonter un serveur de test
    ├── H264_BUILD_GUIDE.md            Recompiler FreeRDP avec support H.264 réel
    └── sessions/                      Journal détaillé, une session par fichier (index : README.md)
```

Le package `asyncrdp` (sous `src/`) ne dépend que du module compilé `asyncrdp._asyncrdp_cffi` (généré par `build_ffi.py` à l'installation) et de `loguru`. Les fichiers sous `integrations/gtk4/` dépendent en plus de PyGObject (`gi.repository.Gtk/Gdk/GdkPixbuf`) — installer l'extra `pip install asyncrdp[gtk4]` — et sont pensés pour être déplacés tels quels dans le futur plugin RDP de GCM une fois son architecture à plugins en place ; ils ne font aucune hypothèse sur le reste de GCM au-delà de `asyncrdp.Client`. `gcm_gtk4_rdp_session.py` assemble les deux ponts (`gcm_gtk4_display_bridge.py`, `gcm_gtk4_clipboard_bridge.py`) avec la connexion dans un cycle de vie commun ; `gcm_gtk4_demo_viewer.py` en fait un visualiseur autonome et réellement exécutable (`python3 gcm_gtk4_demo_viewer.py <host> <user> <password>`), à la fois exemple d'utilisation et point de départ direct du futur plugin GCM. `src/asyncrdp/usb.py` dépend en plus de `pyusb` (extra `pip install asyncrdp[usb]`), importé paresseusement pour ne jamais casser `import asyncrdp` quand l'extra n'est pas installé.

## Pourquoi du C plutôt que du pur Python ?

FreeRDP n'a pas de binding Python officiel, et le protocole RDP (négociation TLS/NLA, codecs d'image, canaux virtuels) est trop complexe à réimplémenter raisonnablement. Le choix a été cffi (mode API, compilation réelle) + un petit shim C (`src/asyncrdp/_shim.c`) appelant directement l'API C de FreeRDP — les mêmes fonctions qu'utilise `xfreerdp` — plutôt que de spawn `xfreerdp` en sous-processus (ce qui aurait été plus simple mais aurait interdit l'accès direct au framebuffer et des callbacks clipboard propres).

## Installation

Le plus simple, depuis la racine du dépôt cloné :

```bash
./install.sh          # installation standard
./install.sh --dev    # + pytest, pour lancer tests/
./install.sh --gtk4   # + PyGObject, pour integrations/gtk4/
./install.sh --usb    # + pyusb/libusb et règle udev, pour asyncrdp.usb
```

Installe les dépendances système (Debian/Ubuntu) via `apt`, puis le package via `uv pip install --system --break-system-packages -e .` (Ubuntu 23.04+/Debian 12+ refusent une installation système sans ce flag — PEP 668 ; `uv` lui-même est installé automatiquement par le script s'il est absent).

Manuellement, sans le script :

```bash
sudo apt install freerdp3-dev libwinpr3-dev build-essential pkg-config python3-dev
pip install --break-system-packages -e ".[test,gtk4]"   # extras au choix — pip classique fonctionne toujours
```

`pip install` (ou `uv pip install`) compile automatiquement le shim C via `cffi_modules` — pas besoin de lancer `build_ffi.py` séparément.

Pour développer la bibliothèque cœur dans un environnement isolé et reproductible (`uv.lock`, sans les extras `gtk4`/`usb` qui dépendent de paquets système) :

```bash
uv sync              # crée .venv/, installe cffi+loguru + ruff/pytest
uv run pytest tests/
uv run ruff check .
```

## Tests automatisés

```bash
pytest tests/                              # unitaires seulement (pas de serveur nécessaire)

ASYNCRDP_TEST_HOST=<host> \
ASYNCRDP_TEST_USER=<user> \
ASYNCRDP_TEST_PASSWORD=<pass> \
pytest tests/test_integration_live.py -v   # + intégration, contre un vrai serveur RDP
```

Les tests d'intégration sont ignorés proprement (`SKIPPED`) tant qu'`ASYNCRDP_TEST_HOST` n'est pas renseigné — voir [`tests/conftest.py`](./tests/conftest.py).

## Utilisation

### Connexion simple

```python
async with asyncrdp.connect(host, port=3389, username="u", password="p") as client:
    ...
```

### Réglages façon mstsc (`RdpOptions`)

```python
options = asyncrdp.RdpOptions(
    width=1920,
    height=1080,
    color_depth=32,
    redirect_clipboard=True,
    redirect_drives=True,
    drives=[asyncrdp.DriveMapping("home", "/home/alice")],
    redirect_printers=True,
    printers=[asyncrdp.PrinterMapping("MonImprimante", is_default=True)],
    serial_ports=[asyncrdp.SerialMapping("COM1", "/dev/ttyUSB0")],
    audio_playback=True,
    monitors=[
        asyncrdp.MonitorDef(0, 0, 1920, 1080, is_primary=True),
        asyncrdp.MonitorDef(1920, 0, 1280, 1024),
    ],
)
async with asyncrdp.connect(host, username="u", password="p", options=options) as client:
    ...
```

Tous les champs ont un défaut raisonnable — ne préciser que ce qui doit changer. Voir la docstring de `RdpOptions` dans `asyncrdp.py` pour la liste complète.

### Affichage

```python
raw = await client.get_frame()  # BGRA32 brut
width, height = client.frame_size
```

### Clavier / souris

```python
client.keyboard.write("bonjour")
client.keyboard.key_press("return")  # touches nommées : return, tab, escape, f1-f12, flèches...
client.mouse.move(100, 200)
client.mouse.click("left")  # ou button_press()/button_release() séparés pour un drag
client.mouse.scroll(120)  # positif = molette vers le haut
```

### Resize dynamique

```python
client.request_resize(1024, 768)  # le serveur peut clamp/ignorer ; confirmation via les frames suivantes
```

### Clipboard riche (texte / image / fichiers)

```python
client.clipboard.on_remote_text_changed = lambda text: print("reçu:", text)
client.clipboard.on_remote_image_changed = lambda dib: ...  # DIB brut (BITMAPINFOHEADER/V5 + pixels)
client.clipboard.on_remote_files_changed = lambda files: ...  # list[RemoteFileInfo]

client.clipboard.get_local_text = lambda: "mon texte"
client.clipboard.announce_local_text("mon texte")

data = await client.clipboard.request_remote_file_contents(index)
```

Voir [`integrations/gtk4/gcm_gtk4_clipboard_bridge.py`](./integrations/gtk4/gcm_gtk4_clipboard_bridge.py) pour un exemple complet de câblage vers `Gdk.Clipboard` (texte, image, fichiers), et [`integrations/gtk4/gcm_gtk4_demo_viewer.py`](./integrations/gtk4/gcm_gtk4_demo_viewer.py) pour l'assemblage complet (affichage + presse-papier + connexion) dans une vraie fenêtre GTK4 exécutable :

```bash
python3 integrations/gtk4/gcm_gtk4_demo_viewer.py <host> <user> <password> [--drive nom:/chemin/local]
```

## Tests

```bash
# Connexion minimale, sans redirections
python examples/test_connect_minimal.py <host> <user> <password>

# Suite protocole (frames, input, resize, clipboard) — autonome, tourne contre n'importe quel serveur RDP
python examples/test_connect_full.py <host> <user> <password>

# Suite complète, y compris tests nécessitant un accès shell à la session distante
python examples/test_full_suite.py <host> <user> <password> \
    --session-display :11 --session-user rdptest \
    --drive-mount-path /home/rdptest/thinclient_drives/<nom_du_disque>
```

Les deux derniers arguments de `test_full_suite.py` ne sont utiles qu'en environnement de développement avec accès shell à la machine hébergeant la session RDP (typiquement un serveur de test local) — ils ne s'appliquent pas à un usage normal en production.

```bash
# Sonde de redirection de périphériques : annonce une imprimante, un port série,
# un port parallèle et un disque témoin, puis se déconnecte.
python examples/probe_rdpdr_devices.py --host <host> --port 3389
```

La réponse du serveur à chaque périphérique (`STATUS_SUCCESS` ou non) se lit **côté serveur**, pas côté client : elle est traitée à l'intérieur du canal `rdpdr` de FreeRDP et jamais remontée à l'API `asyncrdp`. Voir la docstring du script, et [`docs/sessions/session-29.md`](./docs/sessions/session-29.md) pour un harnais qui la journalise.

## Limitations connues

- **Noms de champs FreeRDP à revérifier** en cas de changement de version : `asyncrdp_shim.c` contient des commentaires signalant les points sensibles (`freerdp_get_last_error_string` vs `_name`, layout des structures `RDPDR_*`, `CLIPRDR_FORMAT_DATA_RESPONSE.common.*`).
- **Ports série et parallèle** : la redirection est implémentée et l'annonce part correctement, mais le canal client `serial`/`parallel` de FreeRDP remplit le champ `DeviceData` de l'annonce MS-RDPEFS, que le canal serveur de FreeRDP refuse (`DeviceDataLength` doit valoir 0 pour ces types). Désaccord interne à FreeRDP, constaté en conditions réelles le 2026-09-16 — hors de portée d'un correctif dans ce projet. Voir [`docs/sessions/session-29.md`](./docs/sessions/session-29.md).
- **Imprimante** : l'annonce est acceptée par un serveur bâti sur le canal RDPDR générique de FreeRDP (`STATUS_SUCCESS`), mais aucun serveur libre ne consomme le job d'impression lui-même — l'API serveur de FreeRDP n'expose d'E/S que pour les disques et les cartes à puce.
- **Formats clipboard riches** : image limitée à `CF_DIB`/`CF_DIBV5` (pas de `CF_DIBV6`/formats PNG natifs) ; fichiers : pas de reprise sur coupure réseau pendant un téléchargement.
- **Presse-papier image GTK4 → applications locales** : nécessite le provider natif `integrations/gtk4/native/`, compilé par `install.sh --gtk4` (ou `python3 build_gir.py` à la main). Sans lui, `ClipboardBridge` se dégrade proprement mais GDK n'annonce alors aucun mime-type sérialisable pour l'image — une autre application locale ne peut pas la récupérer. Contourne un bug de marshaling PyGObject documenté dans [`docs/pygobject-async-vfunc-userdata-bug-report.md`](./docs/pygobject-async-vfunc-userdata-bug-report.md) ; le sens inverse (local → RDP) n'est pas concerné.
- **USB** : le shim transmet une chaîne de sélection à `urbdrc` ; l'énumération (côté appelant, via libusb) est fournie par `asyncrdp.usb`. Nécessite des droits udev sur `/dev/bus/usb/...` pour fonctionner sans root — voir [`udev/70-asyncrdp-usb.rules`](./udev/70-asyncrdp-usb.rules) (installée automatiquement par `install.sh --usb`) et `usb_has_rw_access()` pour diagnostiquer ce point avant de tenter une redirection.
- **Redirections imprimante/série/parallèle** : dépendent entièrement du support serveur — de nombreux serveurs RDP Linux (dont xrdp dans sa configuration par défaut) ne les implémentent pas.
- Voir `docs/features-backlog.md` et `docs/sessions/` pour l'historique complet des bugs trouvés et le détail des zones non testées.


---

## Écosystème Gcm4

Ce dépôt fait partie de l'écosystème Gcm4 :

- **[Gcm4](https://github.com/MathildeDec/Gcm4)** — Application principale (gestionnaire de connexions GTK4)
- **[AsyncVnc4](https://github.com/MathildeDec/AsyncVnc4)** — Bibliothèque cliente VNC
- **AsyncRdp4** (ce dépôt) — Bibliothèque cliente RDP

Roadmap d'intégration : [Gcm4 #106](https://github.com/MathildeDec/Gcm4/issues/106)
Tableau de bord : [Gcm4 #107](https://github.com/MathildeDec/Gcm4/issues/107)
Annonce du projet : [Gcm4 #103](https://github.com/MathildeDec/Gcm4/issues/103)
Liaison plugin : [Gcm4 #105](https://github.com/MathildeDec/Gcm4/issues/105)

---

## Licence

AsyncRdp4 est distribué sous **GNU General Public License v3.0 ou ultérieure** (`GPL-3.0-or-later`) : voir [`LICENSE`](./LICENSE). Chaque fichier source porte l'en-tête `SPDX-License-Identifier: GPL-3.0-or-later`.

Dépendances (licences compatibles avec la GPLv3) :

| Dépendance | Licence |
| --- | --- |
| libfreerdp3 (FreeRDP, bibliothèque système) | Apache-2.0 |
| GTK 4 (bibliothèque système, extra `gtk4`) | LGPL-2.1-or-later |
| cffi | MIT-0 |
| loguru | MIT |
| PyGObject (extra `gtk4`) | LGPL-2.1-or-later |
| numpy (extra `gtk4`) | BSD-3-Clause (et licences permissives associées) |
| pyusb (extra `usb`) | BSD-3-Clause |
| gbulb, Pillow (tests GTK4 uniquement) | Apache-2.0, MIT-CMU |
