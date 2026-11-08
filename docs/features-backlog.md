---
name: features-backlog
---

[← CLAUDE.md](../CLAUDE.md) — importé automatiquement depuis `../CLAUDE.md` (`@docs/features-backlog.md`), c'est la source unique pour l'état des fonctionnalités et le backlog. Les renvois "voir docs/sessions/" ci-dessous pointent vers l'archive de sessions (`docs/sessions/README.md` pour l'index) — l'ancien fichier monolithique de ce nom n'existe plus.

# features-backlog.md — état des fonctionnalités

Référence rapide. Pour le récit complet (bugs trouvés, procédures de test, comment reproduire), voir [`docs/sessions/`](./docs/sessions/) et [`README.md`](./README.md).

Légende : ✅ testé en conditions réelles avec de vraies données · ⚠️ implémenté mais limité/partiel · ❌ non testable dans l'environnement de développement utilisé (limitation serveur, pas de matériel, etc.) · 🔲 non implémenté

## Connexion et affichage

| Fonctionnalité | Statut | Détail |
|---|---|---|
| Connexion TLS/NLA | ✅ | Revalidée le 2026-09-07 contre un second serveur RDP externe indépendant (192.168.105.141, binding recompilé sur cette machine contre FreeRDP 3.14.0) — connexion, frames, clavier/souris pilotant réellement un bureau distant, tout confirmé fonctionnel sur un troisième environnement serveur jamais testé avant. Voir `docs/sessions/` |
| Authentification `domain\user` | ✅ | Champ `domain` sur `connect()` |
| Affichage GDI logiciel (Bitmap/RemoteFX legacy) | ✅ | Frames BGRA32 via `Client.get_frame()` |
| Pipeline graphique moderne RDPGFX / H.264 | ✅ | Nécessite FreeRDP compilé avec `-DWITH_OPENH264=ON` (voir `docs/H264_BUILD_GUIDE.md`) — le paquet système Ubuntu standard ne l'a pas |
| Resize dynamique en cours de session | ✅ | Canal Display Control (`disp`) |
| Multi-écran | ✅ | `RdpOptions.monitors`, résolution totale calculée automatiquement |
| Reconnexion automatique | ✅ | Réglage envoyé, pas testé sur coupure réseau réelle |
| Fuseau horaire client | ✅ | Envoyé automatiquement depuis le système local |

## Entrées

| Fonctionnalité | Statut | Détail |
|---|---|---|
| Clavier (texte Unicode) | ✅ | |
| Clavier (touches nommées : entrée, tab, flèches, F1-F12...) | ✅ | Liste dans `asyncrdp_scancode_from_name` (shim) |
| Disposition clavier (locale) | ✅ | `RdpOptions.keyboard_layout` (KLID Windows) |
| Souris (déplacement, clic, molette, drag) | ✅ | `button_press`/`button_release` séparés pour le drag |

## Presse-papier

| Fonctionnalité | Statut | Détail |
|---|---|---|
| Texte, bidirectionnel | ✅ | Round-trip contenu identique confirmé |
| Image, bidirectionnel | ✅ | Byte-identique confirmé (bug de palette trouvé et corrigé en testant) |
| Fichiers, bidirectionnel, dossiers récursifs | ✅ | Métadonnées + contenu réel confirmés identiques |

## Redirections locales

| Fonctionnalité | Statut | Détail |
|---|---|---|
| Disque | ✅ | Lecture et écriture réelles, les deux sens |
| Imprimante | ⚠️ | **Mise à jour 2026-09-16 : annonce enfin ACCEPTÉE par un vrai serveur** — le canal serveur RDPDR générique de FreeRDP 3.31.0 répond `STATUS_SUCCESS` à l'annonce `RDPDR_DTYP_PRINT` d'`asyncrdp` (74 octets de DeviceData conformes à MS-RDPEPC), résultat reproduit à l'identique sur trois exécutions consécutives contre le harnais décrit dans `docs/sessions/session-29.md`. C'est la première fois du projet qu'un serveur accepte ce type : la correction du client est donc prouvée, plus seulement supposée. Ce qui reste bloqué est entièrement côté serveur FOSS — l'API publique `RdpdrServerContext` n'expose d'entrées d'E/S que pour `Drive*` et `Smartcard*`, donc aucun serveur bâti sur ce canal ne peut consommer un job d'impression, même en réimplémentant les E/S génériques. Historique : client correct ; xrdp répond explicitement "not supported" — confirmé le 2026-09-05 dans le code source actuel de xrdp que c'est structurellement jamais implémenté (pas une histoire de config/CUPS), voir `docs/sessions/`. **Note 2026-09-07** : cette machine physique a une vraie imprimante réseau détectable (`Brother MFC-L3770CDW`, découverte mDNS/IPP via CUPS), mais aucune file locale configurée. **Retesté contre un second vrai serveur RDP externe (192.168.105.141, conteneur Linux/LXQt — pas Windows, malgré des signaux initiaux trompeurs, voir `docs/sessions/`)** : le client annonce bien le périphérique (`device_announce: PRN1`), mais une vérification EN DIRECT dans un vrai terminal ouvert sur ce serveur (pas seulement les logs client) montre que CUPS n'y est même pas installé (`lpstat: command not found`) — aucun moyen d'exploiter la redirection sur ce serveur non plus, pour une raison différente de xrdp mais le même résultat pratique. |
| Port série | ❌ | **Mise à jour 2026-09-16 : cause réelle trouvée, et ce n'est pas celle qu'on croyait** — contre le canal serveur RDPDR générique de FreeRDP (qui, lui, négocie bien ce type : `supported` = `SERIAL|PARALLEL|PRINT|FILESYSTEM` après intersection avec le client), l'annonce est rejetée en `ERROR_INVALID_DATA` avant tout callback : le serveur exige `DeviceDataLength == 0` pour ce type, tandis que le canal CLIENT de FreeRDP (`channels/serial/client/serial_main.c`) y écrit le nom du périphérique + NUL (5 octets pour `COM3`). Désaccord interne à FreeRDP entre son propre client et son propre serveur, rien à corriger dans ce projet (champ rempli par le plugin chargé via `freerdp_client_load_addins()`). Laquelle des deux moitiés a tort n'est pas tranché par ce test. Voir `docs/sessions/session-29.md`. Historique : idem — même confirmation source. **Note 2026-09-07** : cette machine physique n'expose que des `/dev/ttyS0`..`ttyS19` (ports COM hérités émulés par le BIOS/ACPI, standards sur ce type de portable, non raccordés à un vrai périphérique), aucun adaptateur USB-série réel. **Retesté contre le second vrai serveur RDP externe (192.168.105.141)** : le client annonce bien `COM3` (`device_announce`), mais une vérification EN DIRECT dans un vrai terminal serveur montre qu'aucun nœud `/dev/ttyS0` (ni équivalent) n'est créé côté serveur — même conclusion pratique que pour l'imprimante ci-dessus, sur un serveur indépendant. |
| Port parallèle | ❌ | Strictement le même diagnostic que le port série ci-dessus, constaté dans la même session du 2026-09-16 (`LPT1` → 5 octets de DeviceData, `ERROR_INVALID_DATA`). Historique : idem — même confirmation source |
| USB | ⚠️ | Transmission de la sélection à `urbdrc` implémentée. Énumération des périphériques (libusb) via `asyncrdp.usb.list_usb_devices()` (extra `pip install asyncrdp[usb]`, s'appuie sur `pyusb`). **Mise à jour 2026-09-07 (première session sur un vrai poste physique, pas un sandbox jetable)** : testée pour de vrai contre du **vrai matériel USB** (souris/clavier filaires Dell, récepteur sans-fil Logitech, webcam intégrée, contrôleur Bluetooth Intel, hub interne — 8 périphériques, VID:PID vérifiés identiques à `lsusb`) — a immédiatement révélé un **vrai bug** jamais visible avant faute de matériel réel à énumérer : `usb.util.get_string()` lève `ValueError` (« no langid »), pas `usb.core.USBError`/`NotImplementedError` comme seul anticipé, dès qu'on n'a pas les droits udev sur `/dev/bus/usb/...` (le cas normal en utilisateur non-root sans règle udev dédiée) — l'énumération plantait donc entièrement sur du vrai matériel alors qu'elle n'avait jamais pu être exercée que sur une liste vide. Corrigé dans `list_usb_devices()` (voir `docs/sessions/`). Une fois corrigé : les 8 périphériques réels apparaissent bien dans la liste (fabricant/produit à `None`, cohérent avec l'absence de droits udev — pas un défaut), `usb_device_args()` construit bien `id,dev:VVVV:PPPP` pour chacun. **Redirection effective testée contre un vrai second serveur RDP externe (192.168.105.141) le même jour** : la webcam (seul périphérique choisi, jamais la souris/clavier/Bluetooth réels) est bien transmise à `urbdrc`, mais l'ouverture exclusive libusb échoue avec `LIBUSB_ERROR_ACCESS`/`LIBUSB_ERROR_IO` — même cause que ci-dessus (droits udev), un étage plus loin (ouverture, pas seulement lecture des descripteurs). Confirme que c'est une limitation d'environnement (permissions), pas un bug, et qu'échouer ainsi est sûr (aucun risque de détacher par erreur un périphérique d'entrée critique). Reste non confirmé : une redirection USB réussie de bout en bout avec les droits udev nécessaires. **Mise à jour 2026-09-10** : le blocage lui-même (droits udev) était documenté mais rien n'était livré pour le lever — `udev/70-asyncrdp-usb.rules` (règle `TAG+="uaccess"`, installée automatiquement par `install.sh --usb`) comble ce manque, et `usb_has_rw_access()` permet de diagnostiquer le blocage par périphérique avant de tenter une redirection plutôt que de laisser l'échec remonter tard depuis libusb/urbdrc. Testé en logique pure seulement (aucun `systemd-logind`/session graphique active dans ce sandbox pour confirmer que la règle produit bien l'ACL attendue une fois posée sur une vraie machine). Voir `docs/sessions/` |
| Audio (lecture/capture) | ⚠️ | Lecture (côté serveur → client) : négociation ET **charge utile réelle confirmées** le 2026-09-05, voir section dédiée dans `docs/sessions/` — vrais blocs `WAVE_FORMAT_PCM` reçus (`rdpsnd_recv_wave_info_pdu`, numéros de bloc croissants) en jouant un vrai fichier WAV côté session via un vrai pipeline PipeWire. Lecture physique côté client non confirmée (échoue sur `snd_mixer_attach`, faute de carte ALSA virtuelle dans ce sandbox — limitation d'environnement, pas du code testé). **Capture (client → serveur) : négociation ET charge utile réelle confirmées le 2026-09-05 (session suivante)** — un ton de 440 Hz généré côté client (faux micro PipeWire) et capturé par le backend `pulse` du canal `audin` de FreeRDP ressort de `xrdp-source` côté session serveur **acoustiquement identique** (fréquence mesurée 440,0 Hz exacte, amplitude crête 16384 contre 16383,5 attendu, stéréo parfaitement dupliqué) — validation bout en bout jusqu'à un vrai consommateur côté session (`parec`), donc plus complète sur ce point précis que la lecture (dont le dernier maillon physique reste bloqué par l'environnement). Voir `docs/sessions/` |

## Fonctionnalités avancées

| Fonctionnalité | Statut | Détail |
|---|---|---|
| RD Gateway | ⚠️ | Implémenté, pas de passerelle disponible pour tester |
| RemoteApp / RAIL | ⚠️ | Implémenté en mode simplifié (une appli plein écran) — pas de vraie gestion multi-fenêtres |
| Cookie de redirection / load balancing RDS | ⚠️ | Implémenté, pas de ferme RDS disponible pour tester |

## Intégration GTK4 (plugin RDP de GCM)

Mise à jour 2026-11-08 : le plugin RDP de GCM est livré dans le dépôt Gcm4
(`plugins/rdp/`, contrat v1, issues Gcm4 #105/#71) — il importe `integrations/gtk4`
tel quel via la clé `integrations` de `[plugin rdp]` / `ASYNCRDP_GTK4_DIR`.
La réserve asyncio du scopage (#36) est tranchée : installer
`GLibEventLoopPolicy` depuis un plugin d'une application déjà lancée reste sans
effet (constaté le 2026-11-01) ; gcm4 installe la politique avant
`Gtk.Application.run` (`gcm4.ui.boucle`) et le plugin refuse l'ouverture sans
asyncio (`asyncio_disponible()`). Le packaging par entry-point reste différé
(chargement en mode source, Gcm4 #288).

| Fonctionnalité | Statut | Détail |
|---|---|---|
| Widget d'affichage (`Gtk.Picture` + input) | ✅ | Logique pure testée (26 tests, `tests/test_gtk4_bridges.py` + stub `tests/gtk_stub/`, 2026-09-01) **et**, depuis le 2026-09-02, rendu réel confirmé : vraie appli GTK4 sous Xvfb, `RdpView._show_frame()` réelle, capture d'écran X11 (`xwd`, lecture directe des pixels du serveur — indépendante de l'API GDK) montrant les bonnes couleurs à l'écran. Confirme concrètement l'hypothèse `Gdk.MemoryFormat.B8G8R8A8` pour du BGRA32 natif. Voir `tests/test_gtk4_live.py`. **Segfault d'environnement diagnostiqué et contourné le 2026-09-06** (session suivante) : le crash signalé la session précédente sur ce même jour (dès la construction du premier widget GTK4, pas seulement `Gtk.Picture.set_content_fit()`) est causé par l'échec du rendu GL accéléré (DRI3/EGL) sous Xvfb dans ce conteneur — `LIBGL_ALWAYS_SOFTWARE=1` seul suffit à l'éviter. Les 4 tests réels de `tests/test_gtk4_live.py` repassent intégralement avec ce contournement (81+ tests au total, binding cffi réellement compilé contre FreeRDP 3.31.0). Voir `docs/sessions/`, « Segfault GTK4 live diagnostiqué et contourné ». **Mise à jour 2026-09-07 (premier vrai poste physique, vrai écran, vrai GPU)** : `RdpView._show_frame()` rejouée sur le **vrai** écran de la machine (session GNOME/Wayland réelle, fenêtre réelle via XWayland, capture `xwd` de la fenêtre parsée à la main) — les 4 couleurs attendues apparaissent correctement dans les 4 quadrants, **sans** avoir besoin de `LIBGL_ALWAYS_SOFTWARE=1` : confirme que ce contournement était bien spécifique à l'absence de DRI3/EGL exploitable sous Xvfb, pas une exigence générale — le rendu GPU réel fonctionne nativement. Voir `docs/sessions/`. **Mise à jour 2026-09-08** : rejoué intégralement dans un nouveau sandbox (GTK 4.14.5, Xvfb, `LIBGL_ALWAYS_SOFTWARE=1`) — mêmes résultats, aucune régression. Premier run combiné de toute la suite (unitaires + intégration xrdp réelle + GTK4 live réel) dans un seul et même environnement : 100 passed, 0 failed. Voir `docs/sessions/`. **Mise à jour 2026-09-16 (session 30)** : rejoué à l'identique dans un nouveau bac à sable — 108 passed, 2 skipped, 2 xfailed, 0 failed (les chiffres ne sont pas directement comparables : plus de tests existent depuis, notamment RDPDR/session-29). Aucune régression. Au passage, `Pillow` — nécessaire à `test_display_bridge_renders_correct_colors_onscreen`, déjà installée à la main en 2026-09-08 sans jamais être déclarée — a été ajoutée à l'extra `gtk4-test` de `pyproject.toml`. Voir `docs/sessions/session-30.md`. |
| Pont presse-papier (texte/image/fichiers) | ⚠️ | **Bug de packaging trouvé et corrigé le 2026-09-07** (première session sur un vrai poste physique) : `gcm_gtk4_clipboard_bridge.py` fait `import numpy as np` au niveau module sans que `numpy` soit déclaré nulle part (ni l'extra `gtk4` de `pyproject.toml`, ni `install.sh`) — reproduit concrètement (`ModuleNotFoundError`) en installant le paquet à neuf sur cette machine en suivant la procédure documentée. Corrigé en ajoutant `numpy` à l'extra `gtk4`. **Bug historique du mime-type image reconfirmé identique sur un vrai bureau complet** (GTK 4.18.5, tous les loaders gdk-pixbuf usuels + avif/heif/jxl/webp présents, vrai gestionnaire de presse-papier GNOME) : `Gdk.ContentProvider.new_for_value(texture).ref_formats().get_mime_types()` toujours vide — ferme définitivement l'hypothèse « sandbox minimal sans les bons loaders », déjà affaiblie avant cette session. `Gdk.Clipboard.set_texture()` (convenience API qui aurait pu contourner le problème) n'existe même pas dans l'introspection GObject de ce binding (`AttributeError`). Voir `docs/sessions/`. Texte bidirectionnel confirmé le 2026-09-02 avec un client X11 externe réel (`xclip`), pas seulement en interne. Décodage RDP→texture du DIB indexé (régression du bug de palette `bfOffBits`) reconfirmé avec le **vrai** `GdkPixbuf`, pas le stub. Fichiers, sens local→RDP, confirmé le 2026-09-02 (session suivante) avec un `xclip -t text/uri-list` externe réel posant 2 vrais fichiers sur disque (dont un sous-dossier) : chemins reçus par `announce_local_files` identiques, contenu des fichiers intact après coup. Reste non confirmé : l'offre de l'image au presse-papier système à un lecteur externe (limitation d'environnement constatée, voir `docs/sessions/` et le test `xfail` dédié). Fichiers sens RDP→local (téléchargement asynchrone) : **logique désormais couverte par 4 tests dédiés** le 2026-09-05 (`tests/test_gtk4_clipboard_file_download.py`, même principe stub que les autres tests de logique pure — conversion du séparateur, reconstruction de l'arborescence, filtre premier-niveau avant dépôt dans `Gdk.FileList`, parallélisme borné à 4 ; les 4 passent, et 2 mutations manuelles du code testé confirment que ces tests détectent bien une régression). Toujours non couvert en conditions réelles (vrai GTK4 + vrai lecteur externe) — nécessiterait `ASYNCRDP_TEST_GTK4=1`/Xvfb, indisponible dans cette session (sandbox sans accès réseau) ; voir `docs/sessions/`. **Mise à jour 2026-09-06** : l'environnement GTK4 live est de nouveau opérationnel dans une session ultérieure (accès réseau `apt` disponible, segfault d'environnement trouvé et contourné — voir la ligne « Widget d'affichage » ci-dessus) ; le sens local→RDP a été rejoué avec succès en conditions réelles à cette occasion. Le sens RDP→local (téléchargement asynchrone) reste non couvert en conditions réelles — non traité cette session, candidat naturel pour la suivante. **Mise à jour 2026-09-08** : rejoué dans un nouveau sandbox — texte, image (décodage), et fichiers dans les deux sens (dont le cas dossier premier-niveau) tous reconfirmés sans régression. Le bug de mime-type image reste identique (même GTK 4.14.5, rien de nouveau à en tirer) — voir `docs/sessions/`. |
| Pont presse-papier — mise à jour 2026-09-06 (session suivante) : fichiers RDP→local | ✅ | Candidat de la session précédente traité : `tests/test_gtk4_live.py::test_clipboard_bridge_files_download_from_rdp_via_external_reader`, avec `gbulb` intégré le temps de ce test pour que `asyncio.ensure_future()` (appelé nu par `_on_remote_files_changed`) s'exécute réellement sous une vraie boucle GLib. A mis en évidence un **vrai bug applicatif** (pas une limitation GTK4 comme pour l'image) : un dossier de premier niveau dans la sélection n'apparaissait jamais dans la `Gdk.FileList` locale — seul son contenu était recréé sur disque, invisible pour l'utilisateur. Corrigé dans `fetch_one` (`gcm_gtk4_clipboard_bridge.py`) et reconfirmé par un lecteur externe (`xclip -o -t text/uri-list`) recevant désormais les deux bons chemins de premier niveau. Sérialisation GType→mime-type de GTK4 pour `Gdk.FileList` confirmée fonctionnelle au passage (contrairement au cas de l'image). Voir `docs/sessions/`. |
| Assemblage dans une application hôte (`RdpSession` + visualiseur de référence) | ✅ | **Ajouté le 2026-09-18** : jusqu'ici les deux ponts ci-dessus n'étaient validés que séparément (chacun contre un `RdpClient` factice ou réel construit à la main dans son propre test) — jamais réunis dans un seul objet représentant une session RDP complète, ni dans une vraie application. `integrations/gtk4/gcm_gtk4_rdp_session.py` (`RdpSession`) comble ce vide : assemble connexion (via un `connect_fn` injecté, même contrat que `asyncrdp.connect`) + `RdpView.attach()` + `ClipboardBridge()` avec un cycle de vie commun (`connect()`/`disconnect()`), referme la connexion si l'assemblage échoue juste après une connexion réussie plutôt que de la laisser fuiter. `integrations/gtk4/gcm_gtk4_demo_viewer.py` en fait un visualiseur GTK4 autonome et réellement exécutable (CLI + `Gtk.Application`, même recette `gbulb` que la ligne « fichiers RDP→local » ci-dessus) — pas le plugin GCM lui-même (l'architecture à plugins de GCM n'existe pas dans ce dépôt), mais pensé comme point de départ direct pour lui. Couvert en logique pure (`tests/test_gtk4_demo_viewer.py`, +17 tests via le même stub `tests/gtk_stub/` que les deux ponts, `RdpView`/contexte de connexion doublés — orchestration, gestion des échecs de connexion et d'assemblage, idempotence de `disconnect()`, mapping CLI→`RdpOptions` tous couverts). **Non confirmé en conditions réelles** cette session-là : le test live correspondant (`tests/test_gtk4_live.py::test_demo_session_assembles_real_window_display_and_clipboard_bridge`, écrit et suivant la recette `gbulb` déjà validée ci-dessus) n'a pas pu s'exécuter dans ce sandbox — `Gdk.Display.open()` y renvoie silencieusement `None` (aucune exception) faute de module GLX dans son paquet `xvfb` (`dpkg -L xvfb | grep glx` vide), alors qu'un client Xlib pur (`xdpyinfo`, `xterm`) s'y connecte sans problème ; `LIBGL_ALWAYS_SOFTWARE=1` (le contournement DRI3 de la ligne « Widget d'affichage » ci-dessus) ne change rien à ce cas précis, distinct. Voir `docs/sessions/session-31.md` et le paragraphe dédié en tête de `tests/test_gtk4_live.py`. **Mise à jour 2026-09-18 (session 32, nouveau bac à sable)** : ce test **passe** — le Xvfb de ce sandbox-ci expose bel et bien GLX (`xdpyinfo -queryExtensions`, `dpkg -L xvfb | grep glx` reste vide même ici et s'avère donc peu fiable comme test, voir `docs/test-environment.md`). Deux bugs réels trouvés et corrigés au passage, sans lien avec GLX : (1) `install.sh --gtk4` était réellement cassé sur une Ubuntu 24.04 neuve — `uv pip install --system` ignorait le `python3-gi` déjà installé par apt et tentait de compiler le dernier PyGObject PyPI, qui exige `girepository-2.0`, absent des dépôts Ubuntu 24.04 ; corrigé en contraignant `uv` à la version déjà installée par apt ; (2) le test cible passait seul mais échouait dans la suite complète du fichier (`RuntimeError: could not create new GType: RdpView`, collision d'enregistrement GObject entre deux imports du même module dans le même process) — corrigé dans le helper de test concerné, robuste à l'ordre des tests dans les deux sens. Suite `test_gtk4_live.py` complète rejouée après les deux correctifs : 8 passed, 2 xfailed (bugs PyGObject connus, sans lien), 0 failed. Suite complète du projet (hors `test_integration_live.py`, vrai serveur RDP non monté cette session) : 122 passed, 1 skipped, 2 xfailed, 0 failed — aucune régression. Voir `docs/sessions/session-32.md`. |

## Ce qui n'existe pas du tout

- Partage de session façon TeamViewer/VNC (ce n'est pas un manque du client : `asyncrdp` peut se connecter à un serveur qui le supporte — Windows RDS shadow, `xrdp-shadow` — sans code supplémentaire, c'est une capacité serveur, pas client)
- Décodage vidéo autre que H.264 (pas de VP8/VP9 — non standard RDP de toute façon)

## Prochaines étapes suggérées (par urgence)

**⚠️ Haute**
- ~~Nettoyer les erreurs ruff pour rendre le code propre~~ **fait le
  2026-09-09** : config `[tool.ruff]` ajoutée à `pyproject.toml`
  (line-length=120 aligné sur le style existant, sélection resserrée
  `E, F, W, I, UP, B, C4, SIM, RUF` — familles plus opinionées comme
  `S`/`ASYNC`/`PLW`/`EXE`/`PYI` volontairement exclues, hors sujet pour
  ce projet). 75 erreurs initiales ramenées à 0 (`ruff check .` passe
  intégralement). Deux vrais bugs trouvés et corrigés en cours de
  route, pas seulement du style : (1) un `except ... as exc` dans
  `tracing.py` référencé depuis une lambda `loguru(lazy=True)` —
  l'autofix ruff avait d'abord cassé le code en supprimant `as exc`
  sans voir cet usage différé, corrigé en capturant `repr(exc)` avant
  la lambda ; (2) 4 tâches `asyncio.ensure_future(...)` fire-and-forget
  sans référence gardée (`_core.py` x3, `gcm_gtk4_clipboard_bridge.py`
  x1), à risque réel de garbage-collection en plein vol — corrigées
  avec un helper `_spawn()` qui garde une référence forte jusqu'à la
  fin de la tâche. Suite complète rejouée après coup (binding cffi
  réellement recompilé contre FreeRDP 3.31.0, installé via `apt` dans
  ce sandbox) : 90 passed, 13 skipped, 0 failed — aucune régression.
  Voir `docs/sessions/session-21.md`.
- ~~Stub GTK4 pour tester la logique pure des ponts sans display~~ **fait
  le 2026-09-01** : `tests/gtk_stub/` (faux `gi.repository` + faux
  module `asyncrdp`) + `tests/test_gtk4_bridges.py`, 26 tests, tous
  passent (`python3 -m pytest tests/test_gtk4_bridges.py`). Couvre la
  mise à l'échelle souris, le mapping boutons/clavier, l'inversion de
  signe de la molette, et surtout une régression directe du bug de
  palette `bfOffBits` (cf. `docs/sessions/`).
- ~~Exécuter réellement les deux ponts dans une vraie appli GTK4 (rendu
  à l'écran, vrai presse-papier système, vrai décodage BMP par
  gdk-pixbuf)~~ **fait le 2026-09-02** : `tests/test_gtk4_live.py`
  (4 tests, `ASYNCRDP_TEST_GTK4=1` + Xvfb + `xclip`/`xwd`/
  `imagemagick`, voir docstring du fichier pour la mise en place).
  Résultats : (1) rendu écran réel confirmé par capture X11 (`xwd`,
  indépendante de GDK) — couleurs correctes, hypothèse
  `Gdk.MemoryFormat.B8G8R8A8` validée concrètement ; (2) presse-papier
  texte bidirectionnel confirmé avec un `xclip` externe réel dans les
  deux sens ; (3) décodage du DIB indexé 4bpp (bug `bfOffBits`)
  reconfirmé avec le vrai `GdkPixbuf`, pas le stub. Point restant,
  documenté en `xfail` plutôt que caché : dans ce sandbox, GDK ne
  déclare **aucun** mime-type sérialisable pour un `GdkTexture` sur le
  presse-papier système (`ContentProvider.ref_formats()` vide, y
  compris pour `image/bmp` dont le module gdk-pixbuf est pourtant
  présent et « writable ») — limitation d'environnement (paquet PNG/JPEG
  gdk-pixbuf manquant ?), pas un bug de code ; voir `docs/sessions/` pour le
  détail du diagnostic. ~~Non couvert non plus : le pont fichiers en
  conditions réelles (stub seulement)~~ **fait le 2026-09-02 (session
  suivante), sens local → RDP uniquement** : voir section dédiée plus
  bas (`test_clipboard_bridge_files_roundtrip_via_external_client`).
  Reste non couvert : ce même pont fichiers dans l'autre sens (RDP →
  local, téléchargement asynchrone), et l'intégration complète avec
  une vraie connexion RDP asyncio (les tests utilisent un objet
  `RdpClient` factice, volontairement — seul le côté GTK4 était en jeu
  ici).
  **Mise à jour 2026-09-02 (session suivante)** : le pont fichiers,
  sens local → RDP, est désormais couvert en conditions réelles —
  `test_clipboard_bridge_files_roundtrip_via_external_client` dans
  `tests/test_gtk4_live.py`. Deux vrais fichiers sur disque (l'un dans
  un sous-dossier, avec un contenu binaire couvrant les 256 valeurs
  d'octet) sont posés sur le presse-papier système par un `xclip -t
  text/uri-list` **externe**, exactement comme les tests texte/image
  déjà en place ; vérifie que `ClipboardBridge` détecte le changement
  via le signal `Gdk.Clipboard::changed`, lit la `Gdk.FileList`, et
  transmet les bons chemins à `announce_local_files` — et que les
  fichiers eux-mêmes ne sont ni déplacés ni altérés (contenu relu
  identique après coup). ~~Reste non couvert : le même pont dans
  l'autre sens (RDP → local, téléchargement asynchrone via
  `asyncio.ensure_future` dans `_download_and_set_files`), qui
  supposerait une boucle asyncio intégrée à la boucle GLib (`gbulb` ou
  équivalent) — hors périmètre choisi pour cette session.~~
  **Logique pure testée le 2026-09-05** (nouvelle session, sandbox sans
  accès réseau donc sans GTK4/Xvfb/pytest installables) :
  `tests/test_gtk4_clipboard_file_download.py`, 4 tests couvrant la
  conversion du séparateur MS-RDPECLIP et la reconstruction de
  l'arborescence, l'exclusion des dossiers de `request_remote_file_contents`,
  le filtre premier-niveau avant `Gdk.FileList`, et le parallélisme
  borné à 4 — les 4 passent, et deux mutations manuelles du code testé
  (borne du sémaphore, filtre premier-niveau), chacune restaurée
  aussitôt après, confirment que ces tests détectent bien une
  régression réelle. ~~Reste ouvert, inchangé : la validation en
  conditions réelles (vrai GTK4 + vrai lecteur externe, comme pour le
  sens local → RDP ci-dessus) nécessiterait `ASYNCRDP_TEST_GTK4=1` —
  pas de couplage à `gbulb` requis pour *ce* niveau de test (le code
  testé ici n'a pas été modifié).~~ **fait le 2026-09-06 (session
  suivante)** : `gbulb` intégré cette fois (voir `docs/sessions/` pour le
  détail de la bonne API — `loop.run_forever(application=app)`, pas
  `app.run()` seul), nouveau test
  `test_clipboard_bridge_files_download_from_rdp_via_external_reader`
  dans `tests/test_gtk4_live.py`. A trouvé un vrai bug (pas une
  limitation GTK4) : un dossier de premier niveau annoncé par le
  serveur n'atteignait jamais la `Gdk.FileList` locale — corrigé dans
  `fetch_one` (`gcm_gtk4_clipboard_bridge.py`), reconfirmé par un
  lecteur externe (`xclip -t text/uri-list`) recevant désormais les
  deux bons chemins de premier niveau. Les tests de logique pure
  ci-dessus ont été mis à jour en conséquence (mêmes 4 tests, dont 2
  modifiés pour refléter le nouveau comportement correct). Voir
  `docs/sessions/`.
- ~~Automatiser plus robustement le test disque (`test_full_suite.py`) —
  actuellement sensible au cycle de vie de session, cause exacte non
  identifiée (probablement lié à la réutilisation d'une session X déjà
  connectée une fois).~~ **fait le 2026-09-04 (nouvelle session)** :
  la cause n'était pas le cycle de vie de session — quatre bugs réels
  et distincts ont été trouvés et corrigés, testés contre un vrai
  serveur xrdp + FreeRDP compilé, **3 exécutions consécutives réussies
  sur une même session réutilisée à chaque fois** (10/10 tests OK à
  chaque run, `exit code: 0`) :
  1. Le point de montage FUSE est créé par `xrdp-chansrv` en tant que
     l'utilisateur de session, sans `allow_other` — un accès direct
     depuis un utilisateur différent (root compris) échoue en
     `Permission denied`. Corrigé : tout accès au montage distant passe
     désormais par `su <session-user> -c ...`.
  2. `/dev/fuse` peut redevenir root-only (`600`) entre deux mises en
     place d'environnement de test — sans lui, aucun montage FUSE n'est
     possible pour l'utilisateur de session, quelle que soit la
     fraîcheur de la session. Pas un bug du code testé, mais un piège
     d'environnement à vérifier avant de conclure à une régression.
  3. xrdp/FreeRDP **tronque le libellé du disque à 8 caractères**
     (`asyncrdptest` → `asyncrdp` sur le disque réel, cohérent avec le
     champ `PreferredDosName` de MS-RDPEFS), silencieusement, sans
     erreur qui l'indique. Le script devinait un chemin fixe qui ne
     correspondait jamais au vrai nom monté. Corrigé : découverte
     dynamique du nom réel (diff du contenu du dossier parent
     avant/après connexion) plutôt qu'un chemin supposé — `--drive-
     mount-path` remplacé par `--drive-mount-parent`.
  4. **La cause la plus profonde, celle qui ressemblait le plus à une
     histoire de « cycle de vie de session »** : lire un fichier DANS
     le montage FUSE (pas juste lister le dossier parent) oblige
     `chansrv` à faire un aller-retour RDPDR vers ce client asyncrdp
     pour aller chercher le contenu réel du fichier local exposé. Le
     faire de façon synchrone/bloquante (`subprocess.run` direct)
     depuis la coroutine qui tient la connexion gèle la boucle asyncio
     — donc plus rien ne traite les fds d'asyncrdp, donc la requête
     RDPDR de chansrv ne reçoit jamais de réponse, donc la commande
     distante bloque indéfiniment : interblocage circulaire pur et
     simple. Corrigé avec `asyncio.to_thread`.
  Voir `docs/sessions/` pour le détail complet du diagnostic, essai par
  essai (y compris les fausses pistes écartées en cours de route).

**⚠️ Haute**
- ~~Diagnostiquer le segfault reproductible de `tests/test_gtk4_live.py`
  signalé le 2026-09-06 (session précédente, même jour), qui empêchait
  toute validation en conditions réelles de l'intégration GTK4~~ **fait
  le 2026-09-06 (session suivante)** : reproduit et diagnostiqué avec
  `gdb` — le crash a lieu dès la construction du tout premier widget
  GTK4 (`Gtk.Window()` ou `Gtk.ApplicationWindow()` via le chemin
  d'initialisation correct `Gtk.Application.run()`, pas spécifiquement
  `Gtk.Picture.set_content_fit()` comme l'hypothèse initiale le
  suggérait), avec un déréférencement de pointeur nul dans
  `libgtk-4.so.1` juste avant un appel à `g_type_interface_peek`
  (trace complète dans `docs/sessions/`). Piste fontconfig (cache froid)
  écartée par test (`fc-cache -f` ne change rien). Piste confirmée :
  échec du rendu GL accéléré (DRI3/EGL indisponible pour Xvfb dans ce
  conteneur — `libEGL warning: DRI3 error: Could not get DRI3 device`
  visible une fois le renderer cairo forcé) — `LIBGL_ALWAYS_SOFTWARE=1`
  seul (rendu logiciel Mesa) suffit à éviter le crash, sans avoir
  besoin de forcer en plus `GSK_RENDERER=cairo` (testé indépendamment).
  Effet de bord noté au passage : le crash tue Xvfb lui-même, pas
  seulement le process Python — nécessite de relancer Xvfb entre deux
  essais. Découverte annexe distincte : le drapeau interne `initialized`
  de l'override PyGObject `gi.overrides.Gtk` (calculé une seule fois à
  l'import via `Gtk.init_check()`) est `False` dans cet environnement,
  alors qu'un appel manuel ultérieur à `Gtk.init_check()` dans le même
  process renvoie `True` sans que cela ne corrige quoi que ce soit
  côté natif — un piège si on essaie de contourner l'erreur propre
  `RuntimeError: Gtk couldn't be initialized` en repatchant ce drapeau
  plutôt qu'en réglant la vraie cause (le rendu GL). Avec le
  contournement `LIBGL_ALWAYS_SOFTWARE=1`, `tests/test_gtk4_live.py`
  repasse intégralement (4 passed, 2 xfailed — les 2 xfailed sont le
  bug connu de mime-type presse-papier image, sans lien) et la suite
  complète (`pytest tests/`, binding cffi réellement compilé contre
  FreeRDP 3.31.0 installé cette session) donne 85 passed, 5 skipped
  (tests d'intégration nécessitant un vrai serveur RDP, non monté
  cette session), 2 xfailed, 0 failed. Voir `docs/sessions/` pour la trace
  gdb complète et le détail de chaque étape de diagnostic.
- ~~Corriger la régression du stub `tests/gtk_stub/asyncrdp.py` qui
  cassait `tests/test_gtk4_bridges.py` et
  `tests/test_gtk4_clipboard_file_download.py` quand lancés isolément
  (exactement comme documenté dans leur propre docstring), depuis
  l'ajout du décorateur `traced` (session 14, 2026-09-06) —
  `ModuleNotFoundError: 'asyncrdp' is not a package`, masqué en
  lançant la suite complète par un accident d'ordre de collection
  pytest.~~ **fait le 2026-09-10** : le vrai `src/asyncrdp/tracing.py`
  (pur Python, aucune dépendance cffi) est désormais chargé
  explicitement comme sous-module `asyncrdp.tracing` du stub, dans les
  deux fichiers de test concernés. Vérifié réellement, binding
  recompilé cette session (`freerdp3-dev` 3.31.0 via `apt`) : les deux
  fichiers passent isolément sans même que le paquet `asyncrdp` soit
  installé (26 + 5 tests, dont un run direct `python3` sans pytest),
  suite complète rejouée sans régression (96 passed, 0 failed, hors
  `test_integration_live.py`/`test_gtk4_live.py` qui nécessitent un
  vrai serveur RDP / un vrai GTK4+Xvfb, absents de ce sandbox). Voir
  `docs/sessions/session-23.md`.

**🟠 Moyenne**
- ~~Comprendre pourquoi, dans le sandbox actuel, GDK ne déclare aucun
  mime-type sérialisable pour un `GdkTexture` sur le presse-papier
  système (constaté le 2026-09-02, voir la fonction
  `test_clipboard_image_available_to_external_reader` dans
  `tests/test_gtk4_live.py`, marquée `xfail`).
  Le module gdk-pixbuf BMP est présent et « writable »
  (`loaders.cache`), mais `Gdk.ContentProvider.new_for_value(texture).
  ref_formats().get_mime_types()` renvoie une liste vide, y compris
  pour `image/bmp` — donc une autre application locale ne pourrait
  actuellement récupérer l'image collée depuis RDP. À déterminer si
  c'est réparable par un paquet manquant (loader PNG/JPEG gdk-pixbuf ?)
  ou une limitation plus profonde de ce GTK4 précompilé.
  **Mise à jour 2026-09-02 (nouvelle session, sandbox reparti de
  zéro)** : le rejeu du test `xfail` confirme que le symptôme persiste
  à l'identique sur une sandbox Ubuntu 24.04.4 fraîche — mais
  l'hypothèse « loader PNG/JPEG gdk-pixbuf manquant » ci-dessus est
  **invalidée** : `GdkPixbuf.Pixbuf.get_formats()` liste bel et bien
  `png`/`jpeg`/`bmp`/`ico`/`tiff` comme `is_writable()=True` et
  `is_disabled()=False` (ils sont compilés en dur dans
  `libgdk_pixbuf-2.0.so.0` plutôt que fournis en modules séparés —
  d'où leur absence de `loaders.cache`, qui ne liste que les modules
  chargés dynamiquement). Diagnostic affiné : `ref_formats()` est déjà
  vide **avant même tout display/presse-papier** (testé en appelant
  `Gdk.ContentProvider.new_for_value(...).ref_formats()` directement,
  sans passer par `Gdk.Clipboard`), et ce **quelle que soit l'API de
  construction utilisée** — `Gdk.Texture.new_for_pixbuf()` (celle du
  code actuel), `Gdk.Texture.new_from_bytes()` avec du vrai PNG déjà
  encodé, et même `GdkPixbuf.Pixbuf` passé tel quel comme valeur du
  `ContentProvider` (sans aucune `Gdk.Texture`) donnent toutes les
  trois une liste de mime-types vide. Donc : (1) pas un problème de
  paquet manquant, (2) pas un problème de méthode de construction côté
  `_dib_to_texture`/`ClipboardBridge` — ce n'est réparable par aucun
  changement dans ce fichier. Piste non vérifiée faute de temps : le
  registre interne de sérialisation GType→mime-type de GTK4 pourrait
  simplement ne couvrir aucun type lié à l'image dans ce build
  Ubuntu 4.14.5 minimal ; un vrai correctif ressemblerait probablement
  à ce qu'a dû faire Vim pour son GUI GTK4 (`gui_gtk4_cb.c`) —
  implémenter un `GdkContentProvider` custom avec son propre
  `ref_formats()`/`write_mime_type_async()` plutôt que compter sur la
  sérialisation automatique de GTK4. Voir `docs/sessions/` pour le détail
  complet des expériences. Bonus pratique découvert au passage : ce
  fichier de test n'a en réalité jamais eu besoin du binding FreeRDP
  compilé — `RdpClient` n'y sert que de type/`SimpleNamespace`, donc
  `PYTHONPATH` pointé sur un stub `asyncrdp` minimal (repris de
  `tests/gtk_stub/asyncrdp.py`, sans le stub `gi` qui l'accompagne —
  celui-ci masquerait le vrai GTK4) suffit pour exécuter toute la
  suite `test_gtk4_live.py` sans compiler FreeRDP.
  **Mise à jour 2026-09-07 (nouvelle session)** : les deux hypothèses
  laissées ouvertes (`source_object=None` ; référence Python forte
  explicite sur le `provider`) ont été testées, même échec identique.
  Test décisif supplémentaire : remplacer entièrement `Gio.Task` par
  `Gio.SimpleAsyncResult` (API dépréciée, ne touche jamais à
  `Gio.Task`) dans `do_write_mime_type_async`/`_finish` — l'échec
  reste **exactement le même message** `g_task_return_boolean:
  assertion 'G_IS_TASK (task)' failed`, alors que ce code n'appelle
  jamais `Gio.Task`. Des traces ajoutées dans les 4 méthodes du
  `ContentProvider` confirment qu'elles s'exécutent toutes
  intégralement et sans erreur (`do_write_mime_type_finish` compris,
  qui retourne bien `True`) **avant** que l'assertion n'apparaisse.
  **Diagnostic révisé, plus précis que celui du 2026-09-02** : le bug
  n'est très probablement pas dans ce projet (ni dans
  `gcm_gtk4_clipboard_bridge.py`, ni dans la façon de sous-classer
  `Gdk.ContentProvider` en PyGObject) mais dans le code interne de GDK
  lui-même (backend X11 du presse-papier, `gdkclipboard-x11.c` côté
  sources GTK, non consulté — `gitlab.gnome.org` toujours hors de
  portée réseau ici), qui enveloppe apparemment l'appel à notre
  `write_mime_type_async`/`_finish` dans son propre `GTask` interne
  invalide au moment de sa propre complétion. Hors de portée de ce
  projet pour aller plus loin : nécessiterait un GTK4 plus récent que
  le 4.14.5 Ubuntu empaqueté dans ce sandbox, ou une lecture directe
  des sources GDK. Voir `docs/sessions/`, section du 2026-09-07.
  **Mise à jour 2026-09-02 (nouvelle session, tentative de correctif)** :
  essai d'un `GdkContentProvider` custom (inspiré du patch réel de Vim
  pour son GUI GTK4, `gui_gtk4_cb.c`, qui avait dû faire la même chose
  pour la même raison générale) plutôt que de compter sur la
  sérialisation automatique de GTK4. **Moitié du problème résolue,
  moitié restante isolée précisément** : `ref_formats()` /
  `clipboard.get_formats()` rapportent désormais correctement
  `['image/png']` (contre `[]` avant) — donc le registre GType→mime-type
  automatique de GTK4 était bien la première cause, contournable. Mais
  la livraison effective des octets échoue avec `GLib-GIO-CRITICAL:
  g_task_return_boolean: assertion 'G_IS_TASK (task)' failed` +
  `g_object_unref: assertion 'G_IS_OBJECT (object)' failed` dans
  `write_mime_type_async`/`write_mime_type_finish` — reproduit à
  l'identique avec trois stratégies d'écriture différentes (async
  imbriqué, synchrone, et synchrone + référence Python forte explicite
  pour écarter une collecte prématurée côté Python), ce qui écarte à la
  fois le choix de stratégie d'écriture et l'hypothèse de durée de vie
  Python comme causes. Lecteur externe : toujours 0 octet reçu au bout
  du compte. Nouveau test dédié ajouté :
  `test_clipboard_image_custom_content_provider_write_bug` dans
  `tests/test_gtk4_live.py` (`xfail`, capture cet état précis — la
  première assertion, sur les mime-types, passe déjà ; c'est la seconde,
  sur les octets reçus, qui échoue). Code de l'expérience gardé hors du
  pont livré (`gcm_gtk4_clipboard_bridge.py` non modifié) puisque le
  correctif n'est que partiel. Voir `docs/sessions/` pour le détail complet.
  **Mise à jour 2026-09-11 (nouvelle session)** : deux ressources
  absentes des sessions précédentes rendaient possible d'aller plus
  loin ici — le miroir GitHub `GNOME/gtk` (`gitlab.gnome.org` reste
  injoignable, mais pas ce miroir) et un sandbox avec GTK4/Xvfb
  réellement installés. Lecture de source ciblée (tag git `4.14.5`,
  identique au paquet installé) : `gdk/gdkclipboard.c::
  gdk_clipboard_write_async()` crée un `GTask *task` réel et valide,
  puis appelle `gdk_content_provider_write_mime_type_async(content,
  ..., gdk_clipboard_write_done, task)` — `task` est donc un pointeur C
  authentique au moment où notre vfunc `do_write_mime_type_async()` est
  invoquée côté Python ; `gdk_clipboard_write_done()` (le callback)
  appelle `write_mime_type_finish` puis, si succès,
  `g_task_return_boolean(task, TRUE)` — exactement la ligne dont
  l'assertion échoue en pratique. `gdk/x11/gdkclipboard-x11.c` et
  `gdk/x11/gdkselectionoutputstream-x11.c` (chemin `SelectionRequest`
  → flux de sortie → `gdk_clipboard_write_async`) lus en entier : rien
  n'y enveloppe `task` dans un objet supplémentaire, la logique C est
  saine. Expérimentation empirique en conditions réelles pour trancher
  l'hypothèse restante : un `GTask` totalement indépendant
  (`Gio.Task.new(self, cancellable, None, None)`, sans jamais lui faire
  porter le couple `(callback, user_data)` reçu), puis appel manuel et
  direct de `callback` avec ce couple reçu strictement intact — testé à
  la fois en 3 arguments et en 2 (au cas où `gi.CCallback` porterait
  déjà son propre `user_data` capturé en interne). **Résultat, dans les
  deux cas : crash identique**, et fait nouveau confirmé par un simple
  `print()` : `user_data`, tel que reçu côté Python, vaut déjà `None` —
  alors que le `task` C réel est confirmé valide à cet instant précis
  par la lecture de source ci-dessus. **Diagnostic affiné** : la perte a
  lieu avant que ce projet n'y touche, au moment où PyGObject marshalle
  l'appel de ce vfunc précis depuis le C — pas dans la façon dont le
  code Python réutilise ensuite ce qu'il croit avoir reçu. Écarte donc
  définitivement toute piste de correctif écrite uniquement en Python
  dans ce projet, quelle que soit la technique d'écriture. Un vrai
  correctif nécessiterait soit un correctif PyGObject/gobject-
  introspection sur le marshaling du closure `(callback, user_data)` de
  ce vfunc précis, soit une sous-classe `GdkContentProvider` écrite en
  C — les deux hors de portée d'une seule session. Nouveau test ajouté :
  `test_clipboard_image_write_bug_survives_direct_callback_invocation`
  dans `tests/test_gtk4_live.py` (`xfail`, même famille que les deux
  tests voisins). Suite complète rejouée sans régression : 101 passed,
  3 xfailed, 0 failed. Voir `docs/sessions/session-24.md` pour le détail
  complet.
  **Mise à jour 2026-09-11 (session suivante)** : des deux pistes
  proposées, la première a été menée à bien — recherche d'un rapport de
  bug PyGObject existant sur ce marshaling. Plusieurs recherches
  ciblées, y compris directement sur `gitlab.gnome.org` (accessible
  depuis les outils de recherche web, contrairement à `curl` dans ce
  sandbox) : aucun rapport existant trouvé correspondant exactement à
  ce scénario (recherche par mots-clés, donc pas une certitude
  absolue). Deux éléments trouvés en cours de route corroborent le
  diagnostic sans le remplacer : une page d'analyse GNOME recensant
  neuf bugs historiques distincts autour du marshaling GObject pour les
  vfuncs/closures Python (aucun ne correspond précisément au nôtre), et
  une merge request PyGObject décrivant le mécanisme
  `GI_SCOPE_TYPE_ASYNC` des fonctions `GAsyncReadyCallback` — testé côté
  PyGObject uniquement dans le sens Python-appelle-C, jamais dans le
  sens inverse (hypothèse de travail, pas un diagnostic confirmé : le
  code interne de PyGObject lui-même n'a pas été lu). Rapport de bug
  rédigé et prêt à déposer : `docs/pygobject-async-vfunc-userdata-
  bug-report.md` (contenu en anglais, repro minimale indépendante de ce
  projet) — non déposé faute de compte GitLab GNOME disponible ici.
  Reste la seconde piste, plus lourde : une sous-classe
  `GdkContentProvider` écrite en C. Voir `docs/sessions/session-25.md`
  pour le détail complet.~~ **Résolu le 2026-09-14, câblé et vérifié en entier le
  2026-09-15 (session suivante)** : la seconde piste
  envisagée le 2026-09-11 (sous-classe `GdkContentProvider` écrite en C)
  a été tentée avec succès. Nouveau composant
  `integrations/gtk4/native/asyncrdp-image-provider.{h,c}` : un
  `GdkContentProvider` natif (`G_DECLARE_FINAL_TYPE`/`G_DEFINE_FINAL_TYPE`),
  jamais surchargé côté Python — `ref_formats()` annonce `image/png`
  (encodé une seule fois à la construction via
  `gdk_texture_save_to_png_bytes()`, API GTK4 synchrone, pas de PNG fait
  main), `write_mime_type_async()`/`_finish()` réutilisent le pattern
  `GTask` standard de GLib, mais entièrement en C : GDK appelle
  directement le pointeur de fonction de sa vtable, sans jamais passer
  par le marshaling vfunc-Python de PyGObject identifié comme la cause
  exacte le 2026-09-11. Exposé à Python via GObject Introspection :
  `build_gir.py` (nouveau, à la racine, même esprit que `build_ffi.py`)
  compile la bibliothèque partagée, scanne le `.gir` (`g-ir-scanner`,
  avec `--identifier-prefix`/`--symbol-prefix` explicites — sans eux le
  scan élimine silencieusement tous les symboles comme hors namespace),
  patche le chemin de la bibliothèque partagée en absolu dans le `.gir`
  (évite toute dépendance à `LD_LIBRARY_PATH`), puis compile le
  `.typelib`.

  Vérifié par un lecteur externe réel
  (`xclip -selection clipboard -t image/png -o`), exactement le
  protocole qui faisait échouer les trois stratégies précédentes :
  en-tête PNG authentique (`\x89PNG\r\n\x1a\n`) reçu dès la première
  tentative, mime-type `image/png` correctement annoncé.
  `ClipboardBridge._on_remote_image_changed`
  (`gcm_gtk4_clipboard_bridge.py`) utilise désormais ce provider natif
  quand le typelib est disponible (`_load_native_image_provider`), avec
  repli propre vers `Gdk.ContentProvider.new_for_value()` sinon
  (`build_gir.py` jamais exécuté, ou typelib non chargeable) — jamais
  une erreur bloquante.

  Bug distinct trouvé et corrigé en cours de route : fixer
  `GI_TYPELIB_PATH` via `os.environ` au moment de l'import de
  `gcm_gtk4_clipboard_bridge` ne suffit pas — GObject-Introspection ne
  lit cette variable qu'à la toute première opération de la
  `Repository` par défaut du process. Dans cette suite de tests (et
  dans une vraie application GTK4, où `Gtk`/`Gdk` sont nécessairement
  chargés avant ce pont), ce premier appel a déjà eu lieu bien avant que
  ce module ne soit jamais importé — la variable d'environnement arrive
  donc toujours trop tard, avec un échec net
  (`ValueError: Namespace AsyncrdpClipboard not available`) plutôt qu'un
  repli silencieux. Remplacé par
  `GIRepository.Repository.prepend_search_path()`, qui modifie la même
  liste de recherche mais à chaud, quel que soit ce qui a déjà été
  chargé.

  `test_clipboard_image_available_to_external_reader` (désormais un
  vrai test, plus un `xfail` — passe par le vrai `ClipboardBridge`
  livré) et le nouveau
  `test_clipboard_image_native_c_provider_delivers_real_png` (expérience
  isolée sur le mécanisme lui-même, dans `tests/test_gtk4_live.py`)
  confirment tous les deux la résolution par lecteur externe réel. Les
  deux tentatives PyGObject précédentes
  (`test_clipboard_image_custom_content_provider_write_bug`,
  `test_clipboard_image_write_bug_survives_direct_callback_invocation`)
  restent `xfail`, à raison : elles documentent fidèlement une impasse
  réelle, seulement contournée, pas corrigée à la source.
  `install.sh --gtk4` compile désormais ce composant automatiquement
  (paquets `libgtk-4-dev`/`gobject-introspection`/`libgirepository1.0-dev`
  + `python3 build_gir.py`, dégradation en avertissement si la
  compilation échoue). `MANIFEST.in` mis à jour pour inclure
  `build_gir.py` et les sources natives dans une distribution source.
  Suite complète rejouée (`ruff` + `pytest`, GTK4 live inclus sous
  Xvfb) : 103 passed, 5 skipped, 2 xfailed, 0 failed — aucune
  régression. Voir `docs/sessions/session-27.md` pour le détail
  complet.
- ~~Monter un vrai environnement PipeWire dans une session de test pour
  valider l'audio au-delà de la négociation de canal.~~ **fait le
  2026-09-05** : `pipewire` + `wireplumber` + `pipewire-pulse` montés
  manuellement dans la session `rdptest` (openbox ne fait pas
  d'autostart XDG, donc pas de lancement automatique — voir docs/sessions/
  pour la procédure complète), puis `libpipewire-module-xrdp` chargé
  avec les **vraies** variables d'environnement de la session
  (`XRDP_SOCKET_PATH=/run/xrdp/sockdir` notamment — récupérées
  directement depuis `/proc/<pid-chansrv>/environ`, une valeur devinée
  au hasard fait échouer le pont silencieusement, sans aucune erreur
  dans les logs). Un vrai fichier WAV joué côté session via `paplay`
  (donc à travers `xrdp-sink`) a produit un vrai bloc `WAVE_FORMAT_PCM`
  côté client asyncrdp (`rdpsnd_recv_wave_info_pdu`, numéros de bloc
  croissants au fil des relances) — validation réelle de la charge
  utile, au-delà de la seule négociation de format déjà confirmée
  avant. Limite restante, non résolue et non bloquante pour la
  validation demandée : FreeRDP ne parvient pas à ouvrir le mixer ALSA
  local pour rejouer physiquement le son dans ce sandbox
  (`snd_mixer_attach failed`), faute de carte ALSA virtuelle
  (nécessiterait le module noyau `snd-dummy`, hors de portée sans
  privilèges noyau ici) — limitation de ce sandbox client, pas du code
  testé. Capture (sens client → serveur) non testée cette session.
  Voir `docs/sessions/` pour le détail complet, y compris le piège des
  variables d'environnement.
  **Mise à jour 2026-09-05 (session suivante)** : la capture
  (client → serveur), seul sens encore non testé, est désormais
  validée bout en bout — avec une fidélité plus poussée que celle déjà
  établie pour la lecture. Un « faux micro » côté client (sink
  `auto_null` généré automatiquement par PipeWire en l'absence de
  matériel réel, son *monitor* posé comme source par défaut) reçoit un
  vrai ton de 440 Hz via un vrai `paplay`, pendant que la connexion
  `asyncrdp` (`audio_capture=True`) est active. Le canal `audin` de
  FreeRDP charge alors le backend `pulse` (nouveauté par rapport à la
  session précédente : ordre de bascule des backends `audin` découvert
  — `pulse` essayé en premier, puis repli sur `oss` si `pulse`
  indisponible ; jamais documenté avant cette session). Côté session
  serveur, un `parec --device=xrdp-source` **externe**, lancé en
  parallèle, capture ce qui ressort réellement de `xrdp-source` :
  fréquence mesurée 440,0 Hz exacte (comptage des passages par zéro sur
  1s de signal stable), amplitude crête 16384 contre 16383,5 attendu
  (0.5 × 32767), canaux gauche/droit rigoureusement identiques
  (différence nulle) — et silence strict avant/après la fenêtre de
  lecture, sans aucun résidu. Erreur RMS résiduelle d'environ 2 % contre
  la sinusoïde idéale une fois le meilleur déphasage entier retrouvé,
  attribuable à la chaîne de rééchantillonnage interne (PipeWire natif
  en 48 kHz flottant, converti en 44,1 kHz entier 16 bits pour
  `parec`/le canal RDPSND), pas à une perte du canal RDP lui-même.
  Aucune modification de code n'a été nécessaire : `RdpOptions.
  audio_capture` fonctionnait déjà correctement, seul l'environnement
  de test manquait. Contrairement à la lecture (bloquée au dernier
  maillon par l'absence de carte ALSA virtuelle côté client), la
  capture est ici validée jusqu'à un vrai consommateur côté session
  (`parec`), donc sans réserve environnementale restante sur ce point
  précis. Piège annexe reconfirmé : les daemons PipeWire/wireplumber/
  xrdp/sesman lancés en arrière-plan ne survivent pas d'une réponse à
  l'autre dans ce sandbox (état disque/paquets/utilisateur persistant,
  mais processus à relancer intégralement à chaque reprise) — même
  au sein d'une seule et même tâche étalée sur plusieurs réponses, pas
  seulement entre deux sessions de travail distinctes. Voir `docs/sessions/`
  pour le détail complet (mise en place du faux micro, découverte de
  l'ordre des backends `audin`, mesures de fidélité).
- ~~Trouver ou monter un serveur RDP supportant réellement
  imprimante/série/parallèle pour valider ces chemins au-delà de « le
  client parle bien le protocole ».
  **Mise à jour 2026-09-05** : la question sous-jacente — est-ce
  réparable côté configuration du serveur de test (xrdp), par exemple
  en installant/démarrant CUPS différemment — est maintenant tranchée
  **définitivement, avec le code source actuel de xrdp à l'appui**
  (`sesman/chansrv/devredir.c`, upstream `neutrinolabs/xrdp`, version
  0.10.6.1, à jour au 2026-07-07 — donc pas une limitation corrigée
  depuis). Le commentaire d'en-tête du fichier dit littéralement
  « xrdp device redirection - only drive redirection is currently
  supported » depuis sa création en 2013. Dans
  `devredir_proc_client_devlist_announce_req()`, `response_status` est
  initialisé à `STATUS_NOT_SUPPORTED` par défaut pour chaque device
  annoncé, et seuls deux `case` du `switch (device_type)` le
  repassent à `STATUS_SUCCESS` : `RDPDR_DTYP_FILESYSTEM` (disque) et
  `RDPDR_DTYP_SMARTCARD`. Le `default:` qui gère `RDPDR_DTYP_SERIAL`/
  `RDPDR_DTYP_PARALLEL`/`RDPDR_DTYP_PRINT` se contente de construire
  une belle chaîne descriptive pour le message de log (« serial
  port »/« parallel port »/« printer ») puis **ne fait rien d'autre** —
  aucun chemin de code n'existe nulle part ailleurs dans ce fichier
  pour ces trois types de périphérique. Donc : ni CUPS, ni un fichier
  de config xrdp différent, ni une version xrdp plus récente
  (celle-ci est la plus récente au moment de la vérification) ne
  changeront rien — c'est une portée fonctionnelle jamais implémentée,
  pas un bug ni un défaut de configuration de nos tests précédents.
  Reste donc ouvert, mais recadré : la seule façon de valider ces trois
  chemins nécessiterait un serveur RDP **structurellement différent**
  de xrdp (un vrai hôte Windows RDP, ou un autre serveur Linux qui
  implémenterait réellement ces device types) — hors de portée de ce
  sandbox, pas une question de configuration à retenter.
  **Mise à jour 2026-09-15** : piste « un autre serveur Linux »
  explorée pour la première fois (voir `docs/sessions/session-28.md`
  pour le détail complet). Le canal serveur RDPDR **générique** de
  FreeRDP lui-même (`channels/rdpdr/server/`, code source upstream lu
  directement) accepte bien ces trois types par défaut au niveau de la
  négociation (`context->supported = UINT16_MAX`, callbacks
  `OnPrinterCreate`/`OnSerialPortCreate`/`OnParallelPortCreate` dédiés)
  — contrairement à xrdp. Mais le traitement des E/S réel
  (CREATE/READ/WRITE, donc les octets d'un job d'impression ou d'un
  flux série) y est explicitement non implémenté, pour tous les types
  de périphérique, dans les sources FreeRDP actuelles ; le serveur
  d'exemple officiel (`server/Sample`) ne câble même pas ce canal. Un
  harnais de test a été commencé (compile et link correctement) mais
  la connexion réseau réelle s'est heurtée à un problème d'écoute TCP
  propre à ce sandbox, non résolu — reprise prévue dans un environnement
  où l'écoute locale est fiable. Conclusion affinée : ce n'est plus
  « xrdp spécifiquement refuse », mais « aucune implémentation FOSS
  prête à l'emploi ne consomme ces types de bout en bout aujourd'hui,
  les construire serait un vrai travail protocolaire ».~~
  **Tranché le 2026-09-16 (session suivante)** — voir
  `docs/sessions/session-29.md` pour le détail complet, y compris la
  procédure de reconstruction du harnais.

  D'abord, le blocage de la session 28 était un **mauvais diagnostic** :
  l'écoute TCP locale fonctionne parfaitement dans ce type de bac à
  sable (vérifié par un `bind`/`listen`/`accept` Python avec vrai
  aller-retour d'octets). Deux méprises se cumulaient : `--local-only`
  ouvre un socket **AF_UNIX**, pas TCP — d'où son absence normale de
  `/proc/net/tcp` — et le message `[freerdp_listener_open]: socket`
  n'est pas fatal (c'est un `continue` dans la boucle sur les
  `addrinfo` : l'IPv6 échoue, l'IPv4 réussit, la fonction retourne
  vrai). Sans `--local-only`, le harnais écoute bien sur
  `0.0.0.0:13389`.

  Deux obstacles réels ensuite, tous deux dans la partie « démo
  graphique » du serveur d'exemple et sans rapport avec RDPDR :
  `test_peer_load_icon()` puis `test_peer_draw_background()` refusent
  un client qui n'annonce ni `RemoteFxCodec` ni `NSCodec` — ce qui est
  le cas d'`asyncrdp` (GDI + RDPGFX) — et coupaient la session avant
  tout échange de périphériques. Dégradés en avertissements côté
  harnais.

  **Résultat, reproduit à l'identique sur trois exécutions :**
  l'imprimante obtient `STATUS_SUCCESS` (une première dans ce projet,
  voir la ligne « Imprimante » plus haut), le disque témoin aussi ;
  série et parallèle sont rejetés en `ERROR_INVALID_DATA` par un
  désaccord entre le client FreeRDP (qui remplit `DeviceData` avec le
  nom du périphérique) et le serveur FreeRDP (qui exige
  `DeviceDataLength == 0` pour ces deux types). La négociation, elle,
  laisse bien passer les trois types (`supported` = `0x000f` après
  intersection avec les capacités du client).

  **Ce qui reste, et pourquoi il n'y a plus de piste à explorer :**
  l'API publique `RdpdrServerContext` n'expose des fonctions d'E/S
  appelables par le serveur que pour `Drive*` et `Smartcard*` — rien
  pour imprimante/série/parallèle. Il n'existe donc même pas de surface
  d'API par où un serveur bâti sur ce canal enverrait un job
  d'impression, indépendamment du fait que les E/S génériques
  CREATE/READ/WRITE soient non implémentées. Valider ces trois chemins
  de bout en bout exige un vrai hôte Windows RDP, ou d'écrire
  soi-même l'implémentation protocolaire manquante — plus une question
  de trouver le bon serveur FOSS.

  Sonde reproductible livrée : `examples/probe_rdpdr_devices.py`.
- ~~Revérifier tous les noms de champs signalés dans `asyncrdp_shim.c` si
  la version de FreeRDP change~~ **fait le 2026-09-02** (revérification à
  froid, sans binding compilé ni serveur — voir `docs/sessions/`) : les
  39 identifiants d'API/macros FreeRDP référencés dans `_shim.c`
  (fonctions `freerdp_*`/`gdi_*`, macros `RDPDR_DTYP_*`, `CB_RESPONSE_*`,
  types `RDPDR_PRINTER`/`RDPDR_DRIVE`/`CLIPRDR_FORMAT_DATA_RESPONSE`/
  `DISPLAY_CONTROL_MONITOR_LAYOUT`, clés de settings
  `FreeRDP_MonitorDefArray`/`FreeRDP_ClientTimeZone`/
  `FreeRDP_LoadBalanceInfo`) existent tous, inchangés, dans la branche
  `master` actuelle de FreeRDP/FreeRDP sur GitHub — aucun renommage
  détecté. Seule nuance notée : le champ brut `MonitorDefArray` de la
  struct interne des settings est désormais marqué
  `SETTINGS_DEPRECATED` côté FreeRDP — sans conséquence ici, `_shim.c`
  passe déjà par l'accesseur `freerdp_settings_set_pointer_len()` et non
  par un accès direct au champ. **Mise à jour 2026-09-02 (session
  suivante)** : contrairement à l'hypothèse ci-dessus, `freerdp3-dev`
  et `libwinpr3-dev` **sont** installables via `apt` dans ce sandbox
  (`archive.ubuntu.com`/`security.ubuntu.com` accessibles) — l'absence
  constatée plus tôt tenait à ce qu'ils n'avaient simplement pas encore
  été installés, pas à une indisponibilité réelle. Compilation +
  exécution réelles effectuées cette fois (`pip install -e .` contre
  FreeRDP 3.31.0, plus récent que les 3.30.x précédemment testés) :
  compile sans erreur (seulement des avertissements de dépréciation),
  les 46 tests unitaires passent avec le binding réel chargé. Ceci
  complète, avec une confirmation d'exécution, la revérification
  statique ci-dessus. **Correction 2026-09-05** : la phrase ci-dessous
  affirmant qu'« un serveur xrdp réel reste indisponible dans ce
  sandbox » s'est révélée fausse dans des sessions ultérieures — `xrdp`
  + `xrdp-sesman` + `xorgxrdp` s'installent et tournent normalement via
  `apt` (voir la section disque et la section audio de `docs/sessions/` pour
  deux campagnes de tests bout-en-bout complètes contre un vrai
  serveur). L'indisponibilité constatée ici tenait uniquement au fait
  qu'ils n'avaient pas encore été installés dans **cette** session
  précise, pas à une limitation du sandbox lui-même — même erreur de
  raisonnement que celle déjà corrigée une ligne plus haut pour
  `freerdp3-dev`.
  **Mise à jour 2026-09-08** : revérification complète refaite, cette
  fois contre un vrai paquet Ubuntu (`freerdp3-dev`/`libwinpr3-dev`
  `3.31.0+dfsg-0ubuntu0.24.04.1` installés via `apt`), et poussée
  jusqu'à une vraie connexion — ce qui restait ouvert après les deux
  sessions précédentes sur ce sujet. Compilation (`pip install -e .`)
  sans erreur ; seuls des avertissements de dépréciation transverses
  (hérités des headers `freerdp.h`/`codecs.h`/`interlocked.h`/`sspi.h`
  inclus par le shim, aucun n'étant un symbole que `_shim.c` appelle
  lui-même — vérifié par `grep`). `xrdp`/`xrdp-sesman`/`xorgxrdp`
  montés dans ce sandbox : connexion réelle réussie
  (`test_connect_minimal.py`, 3 frames 1280x800), puis
  `tests/test_integration_live.py` exécuté pour de vrai pour la
  première fois (`ASYNCRDP_TEST_HOST=127.0.0.1`) — **5/5 tests
  passent** : frame exacte, clavier/souris, resize dynamique du canal
  `disp`, presse-papier texte, déconnexion propre. Suite complète :
  95 passed, 8 skipped (les 8 restants sont `gtk4_live`, hors
  périmètre de cette session, pas de GTK4/Xvfb installés). Ferme
  définitivement l'item « rejouer une vraie connexion contre un xrdp
  réel avec ce binding recompilé ». Aucun correctif nécessaire — voir
  `docs/sessions/`, « Revérification par vraie compilation + vraie
  connexion xrdp ».

**🟢 Faible**
- Tester réellement l'USB avec un périphérique physique (aucun disponible
  dans le sandbox de développement à ce jour).
  **Mise à jour 2026-09-07** : l'énumération côté libusb (partie qui
  *pouvait* être faite sans matériel réel) est désormais écrite et
  testée — `src/asyncrdp/usb.py` (`list_usb_devices()`,
  `usb_device_args()`, `usb_device_args_auto()`), extra pip dédié
  `asyncrdp[usb]` (`pyusb`) ajouté à `pyproject.toml` et à `install.sh`
  (`--usb`), qui manquaient tous les deux à l'archive livrée alors que
  le module et ses tests (`tests/test_usb.py`, logique pure + un appel
  réel non mocké contre `pyusb`/`libusb` installés dans ce sandbox)
  étaient déjà présents — corrigé dans cette session, voir `docs/sessions/`.
  Reste ouvert, inchangé : un vrai périphérique physique pour confirmer
  qu'il apparaît dans `list_usb_devices()` et qu'il est effectivement
  redirigé une fois passé à `RdpOptions.usb_devices` contre un vrai
  serveur RDP — hors de portée de ce sandbox.
  **Mise à jour 2026-09-07 (session suivante, premier vrai poste physique
  avec du vrai matériel USB attaché)** : `list_usb_devices()` exécutée
  contre 8 vrais périphériques (souris/clavier Dell, récepteur Logitech,
  webcam, Bluetooth Intel, hubs — VID:PID vérifiés identiques à `lsusb`).
  A immédiatement trouvé un vrai bug jamais visible sur une liste vide :
  `usb.util.get_string()` lève `ValueError` (pas `USBError`/
  `NotImplementedError`) sans droits udev sur `/dev/bus/usb/...` (cas
  normal en utilisateur non-root) — l'énumération entière échouait sur
  chaque périphérique réel. Corrigé dans `list_usb_devices()`. Une fois
  corrigé : les 8 périphériques apparaissent bien dans le résultat
  (fabricant/produit à `None`, cohérent avec l'absence de droits udev),
  `usb_device_args()` construit la bonne chaîne pour chacun. Reste
  ouvert, inchangé : la redirection effective contre un vrai serveur RDP
  — `xrdp` non installable dans cette session (pas d'accès root non
  interactif sur cette machine), donc pas de serveur RDP disponible ici
  pour ce test précis. Voir `docs/sessions/`.
  **Mise à jour 2026-09-10** : le blocage identifié ci-dessus (droits
  udev sur `/dev/bus/usb/...`) était documenté depuis la session
  précédente mais rien n'existait encore pour le lever côté projet —
  corrigé cette session avec deux ajouts. (1) `udev/70-asyncrdp-usb.rules`,
  une règle `SUBSYSTEM=="usb", TAG+="uaccess"` qui délègue à
  `systemd-logind` plutôt qu'à un groupe (`plugdev`) à gérer
  manuellement — voir ce fichier pour le détail complet et les
  compromis de sécurité (elle s'applique à tout périphérique USB, pas
  seulement à celui qu'on compte rediriger, faute de connaître le
  VID:PID à l'avance côté GCM ; une variante scopée à un périphérique
  précis est documentée en commentaire dans le fichier). `install.sh
  --usb` l'installe désormais automatiquement (`cp` +
  `udevadm control --reload-rules` + `udevadm trigger`), avec un
  avertissement propre plutôt qu'un échec si `udevadm` est absent (cas
  attendu d'un conteneur/CI sans udev réel — c'était le cas de ce
  sandbox de développement, `udevadm` n'y est pas installé du tout, donc
  ce chemin précis du script n'a pu être vérifié que par lecture, pas
  par exécution réelle). (2) `usb_has_rw_access()` dans `usb.py`, qui
  diagnostique l'accès à un device node donné (via `os.access()`, sans
  ouverture libusb) pour qu'un appelant — le futur plugin GCM —
  le détecte *avant* de tenter une redirection plutôt que de recevoir un
  `LIBUSB_ERROR_ACCESS` tardif et peu clair. Testé en logique pure
  (5 tests dans `tests/test_usb.py`, dont un cas positif contre un vrai
  fichier réel, pas seulement mocké). Ce qui reste ouvert, inchangé sur
  le fond : une confirmation matérielle complète — règle installée, vrai
  périphérique, vrai serveur RDP, dans cet ordre — hors de portée de ce
  sandbox (ni `udevadm`, ni session `systemd-logind` active, ni matériel
  USB physique, ni serveur RDP tiers ne s'y trouvent réunis). Voir
  `docs/sessions/`.
- ~~Porter le décorateur de traçabilité entrée/sortie et le test
  d'absence de dépendances circulaires, identifiés comme écarts réels
  (pas des choix délibérés) lors d'une comparaison avec le catalogue de
  règles d'un autre projet le 2026-09-05~~ **test anti-cycles fait le
  2026-09-05 (session suivante)** : `tests/test_no_circular_imports.py`,
  graphe des imports internes construit par `ast` (pas relu à la main),
  validé acyclique par parcours en profondeur ; détecteur vérifié à la
  fois sur des graphes synthétiques et par une vraie régression
  injectée puis restaurée dans `_core.py` — voir `docs/sessions/`, « Test
  d'absence de dépendances circulaires ajouté ». ~~Reste ouvert : le
  décorateur de traçabilité entrée/sortie (`tracing.py::traced`),
  volontairement laissé de côté cette session (plus invasif — touche
  potentiellement tous les fichiers de `src/`/`integrations/`, alors
  que le test anti-cycle n'ajoute qu'un seul fichier neuf sans toucher
  au code de production).~~ **décorateur fait le 2026-09-06** :
  `src/asyncrdp/tracing.py` (nouveau), décorateur `traced` appliqué à
  54 fonctions/méthodes de `_core.py` et 32 des deux ponts GTK4 (toutes
  sauf `connect()` et les closures imbriquées — voir `docs/sessions/`,
  « Décorateur de traçabilité entrée/sortie ajouté », pour le détail
  des exclusions et de la vérification réelle effectuée, y compris
  contre le binding cffi réellement compilé cette session contre
  FreeRDP 3.31.0). Tous les écarts identifiés dans la comparaison
  `PATTERNS.md` du 2026-09-05 sont donc désormais traités (seul le hook
  pre-commit reste ouvert, jugé hors sujet pour ce projet — absence de
  workflow de commit outillé documentée dans ce dépôt).

**🔵 Revérification (2026-09-18, session 33)** : nouveau bac à sable,
aucune feature codable restant ouverte identifiée — revérification
complète menée à la place. `install.sh --dev --gtk4` de bout en bout
sans aucun correctif nécessaire (les deux bugs de la session 32
tiennent) ; suite complète contre un vrai serveur xrdp local
(recette `docs/test-environment.md`) : 127 passed, 1 skipped,
2 xfailed, 0 failed, `test_integration_live.py` compris (5/5). USB et
imprimante/série/parallèle revérifiés bloqués pour les mêmes raisons
qu'avant (pas de matériel USB physique, pas de session
`systemd-logind` dans ce conteneur). Voir `docs/sessions/session-33.md`.
