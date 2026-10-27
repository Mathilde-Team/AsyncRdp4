# CLAUDE.md — asyncrdp


## Mission

Avant toute intervention autonome sur ce dépôt : lire et appliquer la
mission d'équipe :
https://github.com/Mathilde-Team/.github/blob/dev/MISSION.md
(le socle qualité, SOCLE.md, prime sur la mission en cas de conflit ; si
le fichier est absent sur `dev`, le chercher sur `main`).

Fichier court, à lire en entier à chaque reprise de session. Le détail
(raisonnement, décisions de conception, bugs) est ailleurs — voir
« Pour aller plus loin » en bas, à n'ouvrir qu'au besoin, pas
systématiquement.

`asyncrdp` est un client RDP asyncio (liaison cffi + shim C vers
libfreerdp3), pensé comme l'équivalent RDP d'`asyncvnc2` pour le futur
plugin RDP de GCM (gnome-connection-manager, réécriture en architecture
à plugins). Les fichiers `gcm_gtk4_*.py` dans `integrations/gtk4/`
sont l'intégration GTK4 destinée à ce plugin.

Pour l'API exacte d'une bibliothèque tierce (FreeRDP, GTK4/PyGObject,
uv...) — au-delà de ce que ce fichier et `docs/` couvrent déjà — si un
serveur MCP `context7` est disponible dans l'environnement, l'interroger
pour une documentation à jour plutôt que de deviner depuis la mémoire
d'entraînement, en particulier sur des signatures d'API ou des noms de
paquets susceptibles d'avoir changé entre versions.

## État courant

Bibliothèque compilée, exécutée et testée contre plusieurs vrais
serveurs RDP — pas seulement relue. Dernière suite complète
(2026-09-16, session 30, unitaires + intégration xrdp réelle + GTK4
live réel réunis pour la première fois depuis le 2026-09-08) :
**108 passed, 2 skipped, 2 xfailed, 0 failed**. Au passage, une
dépendance de test manquante depuis 3 sessions (`Pillow`, nécessaire à
un seul test GTK4 live, jamais déclarée bien que déjà rencontrée le
2026-09-08) a été ajoutée à l'extra `gtk4-test` — ce n'est plus une
étape manuelle. Voir `docs/sessions/session-30.md`. Rejoué depuis, hors
intégration réelle contre un serveur xrdp (non remontée) : unitaires +
GTK4 live le 2026-09-18 (session 32, voir ci-dessous) — 122 passed,
1 skipped, 2 xfailed, 0 failed, aucune régression.

**Qualité de code (2026-09-09)** : `ruff check .` passe intégralement
(0 erreur, config `[tool.ruff]` ajoutée à `pyproject.toml`). Deux vrais
bugs trouvés et corrigés au passage (pas seulement du style) : un
`except ... as exc` dont la variable était référencée dans une lambda
différée loguru (cassé une première fois par l'autofix ruff lui-même,
puis corrigé proprement) et 4 tâches asyncio fire-and-forget
(`asyncio.ensure_future`) sans référence gardée, à risque de
garbage-collection en plein vol. Suite complète rejouée après coup
(binding compilé contre FreeRDP 3.31.0) : 90 passed, 13 skipped,
0 failed — aucune régression. Voir `docs/sessions/session-21.md`.

**USB — permissions (2026-09-10)** : le blocage udev documenté depuis le
2026-09-07 (`LIBUSB_ERROR_ACCESS`) était identifié mais rien n'était
livré pour le lever. Ajouté : `udev/70-asyncrdp-usb.rules` (installée
automatiquement par `install.sh --usb`) et `usb_has_rw_access()` pour
diagnostiquer le blocage par périphérique avant une redirection, plutôt
que de laisser l'échec remonter tard depuis libusb/urbdrc. Testé en
logique pure uniquement (`tests/test_usb.py`, +5 tests ; suite complète
rejouée : 96 passed, 12 skipped, 0 failed, aucune régression) — ni
`udevadm` ni session `systemd-logind` active dans ce sandbox pour
valider la règle elle-même sur une vraie machine. Voir
`docs/sessions/session-22.md`.

**Tests GTK4 « logique pure » — régression corrigée (2026-09-10)** :
`tests/test_gtk4_bridges.py` et `tests/test_gtk4_clipboard_file_download.py`
échouaient à la collection quand lancés isolément (exactement comme
documenté dans leur propre docstring), depuis l'ajout du décorateur
`traced` le 2026-09-06 — le stub `tests/gtk_stub/asyncrdp.py` n'avait
jamais reçu de sous-module `tracing` correspondant. Passait inaperçu
en lançant la suite complète (un autre fichier de test peuplait
`sys.modules['asyncrdp.tracing']` avant, par accident d'ordre de
collection). Corrigé en chargeant le vrai `src/asyncrdp/tracing.py`
(pur Python, aucune dépendance cffi) comme sous-module du stub.
Vérifié réellement : binding recompilé (`freerdp3-dev` 3.31.0 via
`apt`), les deux fichiers passent isolément sans le paquet `asyncrdp`
installé (26 + 5 tests), suite complète rejouée sans régression
(96 passed, 0 failed, hors les deux fichiers nécessitant un vrai
serveur RDP / un vrai GTK4+Xvfb, absents de ce sandbox). Voir
`docs/sessions/session-23.md`.

**Revérification complète + diagnostic affiné du bug presse-papier
image (2026-09-11, session suivante)** : environnement de dev remonté
en entier dans ce sandbox (FreeRDP3 3.31.0, GTK4 4.14.5, Xvfb, xclip —
absents du sandbox de la session 23). Suite complète rejouée sans
régression, GTK4 live inclus pour la première fois depuis le
2026-09-08 : 101 passed, 3 xfailed, 0 failed ; `ruff check .` toujours
à 0 erreur. Le bug presse-papier image (xfail connu) a été creusé via
le miroir GitHub `GNOME/gtk` (gitlab.gnome.org reste hors de portée
réseau, mais pas ce miroir) : lecture de `gdk/gdkclipboard.c` +
`gdk/x11/gdkclipboard-x11.c` (tag 4.14.5) et expérimentation empirique
en conditions réelles concordent — le `GTask` interne de GDK est
authentique et valide côté C, mais `user_data` arrive déjà à `None`
côté Python dans `do_write_mime_type_async()`, quelle que soit la
technique employée pour le réutiliser ensuite (testé : appel direct du
callback reçu à la place de `Gio.Task.new(self, cancellable, callback,
user_data)`, même échec identique). Cause désormais pointée sur le
marshaling PyGObject du closure `(callback, user_data)` de ce vfunc
précis, plutôt que « quelque part dans GDK » — écarte définitivement
toute piste de correctif écrite en Python pur dans ce projet. Nouveau
test : `test_clipboard_image_write_bug_survives_direct_callback_invocation`
(xfail, même famille que les deux tests voisins). Voir
`docs/sessions/session-24.md`.

**Rapport de bug PyGObject prêt à déposer (2026-09-11, session
suivante)** : recherche d'un rapport existant sur ce marshaling
(`gitlab.gnome.org/GNOME/pygobject/-/issues`, accessible depuis les
outils de recherche web même si `curl` dans ce sandbox ne l'atteint
pas) — aucun trouvé correspondant exactement à ce scénario. Contenu
prêt à déposer rédigé dans
`docs/pygobject-async-vfunc-userdata-bug-report.md` (en anglais, repro
minimale indépendante de ce projet). Aucun compte GitLab GNOME
disponible ici pour le déposer soi-même. Voir
`docs/sessions/session-25.md`.

**Reprise depuis une archive zip dans une nouvelle conversation
(2026-09-11, session suivante)** : dépôt fourni sous forme d'archive zip
(export de fin de session 25) plutôt que dans la continuité directe du
même bac à sable. Un second bac à sable préexistant, retrouvé au chemin
`/home/claude/work/asyncrdp-pkg` avec le paquet déjà installé en
editable, s'est révélé strictement identique à l'archive (`diff -rq` sur
tous les fichiers hors artefacts de build) — les deux convergent,
travail repris sur ce chemin. Environnement revérifié sans régression :
`ruff check .` à 0 erreur, `pytest tests/` à 96 passed/13 skipped/
0 failed (écart de skips expliqué par la croissance de la suite, pas une
régression). Bug distinct trouvé au passage et corrigé : `install.sh
--usb` avortait silencieusement (`set -e`) quand `udevadm` est présent
mais injoignable (pas de démon udev actif) — cas non couvert par la
garde existante, qui ne prévoyait que « udevadm absent ». Corrigé pour
avertir et continuer au lieu d'abandonner tout le script avant
l'installation du paquet. GTK4 live et intégration réelle contre un
serveur RDP non rejoués cette session (`Xvfb`/`xrdp` non relancés). Voir
`docs/sessions/session-26.md`.

**Presse-papier image GTK4 — résolu par une sous-classe
`GdkContentProvider` écrite en C (2026-09-14 → 2026-09-15, session
suivante)** : la
piste envisagée le 2026-09-11 comme seule issue restante (« hors de
portée sans réponse de PyGObject ou une sous-classe
`GdkContentProvider` écrite en C ») a été tentée et confirmée en
conditions réelles. Nouveau composant `integrations/gtk4/native/`
(`asyncrdp-image-provider.c`) : un `GdkContentProvider` natif, jamais
surchargé côté Python, exposé via GObject Introspection (`build_gir.py`
à la racine, même esprit que `build_ffi.py`) — GDK appelle directement
le pointeur de fonction C de sa vtable pour `write_mime_type_async`/
`_finish`, sans jamais traverser le marshaling vfunc-Python de
PyGObject en cause. Vérifié par un lecteur externe réel
(`xclip -t image/png`) : en-tête PNG authentique reçu, là où les trois
stratégies PyGObject précédentes échouaient toutes à l'identique avec
un `GTask` invalide côté Python. `ClipboardBridge._on_remote_image_changed`
s'en sert désormais quand le typelib est disponible, avec repli propre
vers l'ancien comportement sinon (`_load_native_image_provider`) —
jamais une erreur bloquante. `test_clipboard_image_available_to_external_reader`
(le test qui passe par le vrai pont livré) et le nouveau
`test_clipboard_image_native_c_provider_delivers_real_png` (l'expérience
isolée sur le mécanisme) ne sont plus des `xfail` ; les deux tentatives
PyGObject précédentes le restent, à raison — trace fidèle d'une impasse
réelle, seulement contournée.

Bug distinct trouvé et corrigé au passage : fixer `GI_TYPELIB_PATH` via
`os.environ` après coup ne suffit pas — GObject-Introspection ne lit
cette variable qu'à la toute première opération de la Repository par
défaut du process, déjà consommée par le premier
`gi.require_version("Gtk", "4.0")` de la suite de tests (et, en usage
réel, par le bootstrap GTK4 de l'application hôte). Remplacé par
`GIRepository.Repository.prepend_search_path()`, qui agit à chaud quel
que soit ce qui a déjà été chargé avant.

`install.sh --gtk4` compile désormais ce composant automatiquement
(`libgtk-4-dev`/`gobject-introspection`/`libgirepository1.0-dev` +
`python3 build_gir.py`, dégradation en avertissement si la compilation
échoue, jamais un script interrompu). `MANIFEST.in` mis à jour pour
inclure `build_gir.py` et les sources natives dans une distribution
source. Suite complète rejouée (`ruff` + `pytest`, GTK4 live inclus
sous Xvfb) : 103 passed, 5 skipped, 2 xfailed, 0 failed — aucune
régression. Voir `docs/sessions/session-27.md`.

**Imprimante/série/parallèle — question tranchée (2026-09-16, session
suivante)** : la piste parquée en session 28 a été menée à son terme. Le
blocage d'alors (« probablement une restriction du bac à sable sur les
sockets en écoute », hypothèse marquée non confirmée) était un **mauvais
diagnostic** : l'écoute TCP locale fonctionne, `--local-only` ouvre un
socket AF_UNIX (d'où son absence de `/proc/net/tcp`) et le message
`[freerdp_listener_open]: socket` est un `continue` sur l'échec IPv6, pas
un échec d'ouverture. Harnais relancé sans `--local-only`, deux obstacles
réels levés (`test_peer_load_icon()` puis `test_peer_draw_background()`
refusent tout client sans RemoteFX/NSCodec — donc `asyncrdp`, qui fait du
GDI + RDPGFX). Résultat, identique sur trois exécutions : **l'imprimante
obtient `STATUS_SUCCESS`** — première fois du projet qu'un serveur accepte
ce type, la correction du client est donc prouvée et plus seulement
supposée ; **série et parallèle sont rejetés en `ERROR_INVALID_DATA`** par
un désaccord interne à FreeRDP (son client écrit le nom du périphérique
dans `DeviceData`, son serveur exige `DeviceDataLength == 0` pour ces deux
types). Plus rien à explorer côté serveur FOSS : l'API publique
`RdpdrServerContext` n'expose d'E/S que pour `Drive*` et `Smartcard*`,
donc aucune surface par où envoyer un job d'impression. Sonde livrée :
`examples/probe_rdpdr_devices.py`. Voir `docs/sessions/session-29.md`.

**Assemblage GTK4 dans une vraie application hôte (2026-09-18, session
suivante)** : le seul candidat resté ouvert après la session 30
(« consolider le plugin RDP de GCM ») a été traité — GCM lui-même
restant indisponible dans ce dépôt, sous la forme d'un visualiseur de
référence autonome plutôt que du vrai plugin GCM. Livrés :
`integrations/gtk4/gcm_gtk4_rdp_session.py` (`RdpSession`, assemble
connexion + `RdpView` + `ClipboardBridge` avec un cycle de vie commun,
`connect_fn` injecté plutôt qu'un appel en dur à `asyncrdp.connect`,
fermeture de la connexion si l'assemblage échoue après une connexion
réussie plutôt que de la laisser fuiter) et
`integrations/gtk4/gcm_gtk4_demo_viewer.py` (CLI + `Gtk.Application`
réels, même recette `gbulb` que le test existant). Testé en logique
pure (`tests/test_gtk4_demo_viewer.py`, +17 tests, doublure
`connect_fn`/`RdpView`/contexte async, aucun display ni serveur requis)
et par relecture attentive pour le câblage `Gtk.Application` lui-même —
**pas** en conditions réelles (voir ci-dessous). Suite de logique pure
rejouée sans régression (`test_gtk4_bridges.py` +
`test_gtk4_clipboard_file_download.py` + `test_no_circular_imports.py`
étendu aux deux nouveaux modules : 55 passed au total) ; `ruff check .`
toujours à 0 erreur (a lui-même détecté une vraie tâche
fire-and-forget non gardée dans le test live ajouté, avant même
exécution — RUF006). Tentative de vérification live : bloquée par une
découverte d'environnement distincte du piège DRI3 déjà connu — le
Xvfb de ce sandbox n'a aucun module GLX
(`dpkg -L xvfb | grep glx` vide), ce qui fait échouer silencieusement
`Gdk.Display.open()` (`None`, sans exception) y compris depuis un
`Gtk.Application.run()` réel, alors qu'un client Xlib pur (`xdpyinfo`,
`xterm`) se connecte sans problème — `LIBGL_ALWAYS_SOFTWARE=1` ne peut
rien y faire (voir le paragraphe dédié en tête de
`tests/test_gtk4_live.py`). Le test live correspondant
(`test_demo_session_assembles_real_window_display_and_clipboard_bridge`)
est écrit, suit la recette gbulb déjà validée, mais reste à exécuter
pour de vrai dans un environnement où GLX est disponible. Voir
`docs/sessions/session-31.md`.

**Assemblage GTK4 — revalidé en conditions réelles, deux bugs réels
trouvés et corrigés au passage (2026-09-18, session suivante, nouveau
bac à sable)** : reprise directement sur le seul point resté ouvert
après la session 31. Le Xvfb de CE sandbox expose bel et bien GLX
(confirmé par `xdpyinfo -queryExtensions`, voir
`docs/test-environment.md` — `dpkg -L xvfb | grep glx` reste vide même
ici, ce test s'avère donc peu fiable) : contrairement à la session 31,
`test_demo_session_assembles_real_window_display_and_clipboard_bridge`
a pu être exécuté pour de vrai. Deux bugs réels et indépendants
trouvés et corrigés au passage, aucun des deux lié à GLX :

1. `install.sh --gtk4` échouait réellement sur une Ubuntu 24.04 neuve
   (pas seulement dans ce sandbox) : `uv pip install --system` ignore
   le `python3-gi` déjà installé par apt (pas de métadonnées pip pour
   un paquet dpkg) et tente de compiler le dernier PyGObject PyPI
   (3.58.0), qui exige `girepository-2.0` — absent des dépôts Ubuntu
   24.04. Corrigé en contraignant `uv` à la version déjà installée par
   apt plutôt que de laisser PyPI résoudre la plus récente. Voir
   `docs/test-environment.md`.
2. Le test cible passait seul mais échouait dans la suite complète du
   fichier (`RuntimeError: could not create new GType: RdpView`) —
   `class RdpView(Gtk.Widget)` enregistre un GType global au process,
   qu'un test antérieur (`test_display_bridge_renders_correct_colors_onscreen`)
   avait déjà enregistré via un import réel ; le helper de substitution
   `_import_display_and_session_with_stub_asyncrdp()` rejouait
   inconditionnellement cette définition de classe, provoquant une
   double inscription. Corrigé dans ce helper : réutilise le module
   déjà chargé s'il est présent, le laisse résident sinon — robuste à
   l'ordre des tests dans les deux sens (voir le commentaire dédié dans
   `tests/test_gtk4_live.py`).

Suite `test_gtk4_live.py` complète rejouée après les deux correctifs :
8 passed, 2 xfailed (les deux bugs PyGObject connus, sans lien), 0
failed. Suite complète du projet rejouée (hors `test_integration_live.py`,
qui nécessite un vrai serveur RDP, non monté cette session) : 122
passed, 1 skipped, 2 xfailed, 0 failed — aucune régression ; `ruff
check .` toujours à 0 erreur. Le backlog ne comporte plus aucun point
bloqué par l'environnement côté GTK4 — seuls USB et imprimante/série/
parallèle (bloqués par du matériel/protocole externe, sans rapport)
restent ouverts. Voir `docs/sessions/session-32.md`.

**Revérification complète, nouveau bac à sable, aucun bug trouvé
(2026-09-18, session suivante)** : reprise depuis une nouvelle archive
zip, faute de feature codable restant ouverte dans le backlog.
`./install.sh --dev --gtk4` rejoué de bout en bout sans aucun correctif
nécessaire cette fois (les deux bugs de la session 32 — contrainte
PyGObject, collision de GType — tiennent). Suite complète rejouée
contre un vrai serveur xrdp local (recette `docs/test-environment.md`,
utilisateur `rdptest`, services démarrés et vérifiés vivants dans le
même appel outil que la suite de tests) : **127 passed, 1 skipped,
2 xfailed, 0 failed**, `test_integration_live.py` compris (5/5) —
aucune régression, aucun bug trouvé. USB et imprimante/série/parallèle
revérifiés bloqués pour les mêmes raisons qu'avant (pas de matériel USB
physique, pas de session `systemd-logind` dans ce conteneur). Voir
`docs/sessions/session-33.md`.

Validé en conditions réelles : connexion/TLS/NLA, affichage (GDI +
pipeline RDPGFX/H.264), clavier/souris, resize dynamique, presse-papier
texte/image (bidirectionnel, image via lecteur externe réel)/fichiers
(bidirectionnel), disque redirigé, multi-écran, audio lecture +
capture, intégration GTK4 (affichage + ponts, séparément).

Ouvert, mais bloqué par l'environnement plutôt que par du code à
corriger :
- **USB** — énumération testée sur du vrai matériel (bug trouvé et
  corrigé) ; règle udev + diagnostic de permissions livrés le
  2026-09-10 pour lever le blocage identifié, mais redirection de bout
  en bout toujours pas confirmée, faute d'une machine réunissant à la
  fois la règle installée, du vrai matériel et un vrai serveur RDP
- **Imprimante / série / parallèle** — tranché le 2026-09-16 (voir
  ci-dessus) : le client est correct pour l'imprimante (prouvé), série
  et parallèle butent sur une incohérence interne à FreeRDP. Aucun
  serveur FOSS ne consomme ces types de bout en bout, et il n'existe
  pas d'API serveur pour le faire — un vrai hôte Windows RDP, ou écrire
  l'implémentation protocolaire manquante, sont les seules suites
  possibles

L'assemblage GTK4 (RdpSession + DemoApp), seul troisième point encore
ouvert après la session 31, a été revalidé en conditions réelles le
2026-09-18 (voir ci-dessus) — plus aucun point ouvert côté GTK4. Une
revérification complète supplémentaire (session 33, même jour, nouveau
bac à sable) confirme cet état sans rien trouver de nouveau à corriger.

Détail complet, trié par urgence : `docs/features-backlog.md`, importé
ci-dessous.

## Prochaine étape

Outillage : rien à migrer. Ce projet n'a jamais utilisé Poetry, et la
bascule vers `uv` livrée le 2026-09-15 a été revérifiée le 2026-09-16
dans un bac à sable neuf (`uv sync` recompile le shim cffi contre FreeRDP
3.31.0, `install.sh` amorce `uv` lui-même s'il est absent). `ruff check .`
à 0 erreur.

Le backlog (`docs/features-backlog.md`) reste **entièrement traité ou
tranché**. Rien n'y est ouvert faute de travail restant à faire —
seulement faute d'environnement ou de matériel adéquat, et ce n'est
plus vrai que de deux points (le troisième, l'assemblage GTK4, a été
revalidé en conditions réelles le 2026-09-18, voir « État courant ») :

- **USB** — il faut une machine réunissant la règle udev installée, du
  vrai matériel et un vrai serveur RDP. Rien à coder d'ici là.
- **Imprimante / série / parallèle** — tranché le 2026-09-16 (voir
  « État courant »). Les suites possibles sortent du périmètre d'une
  session : un vrai hôte Windows RDP pour valider le chemin imprimante
  de bout en bout, ou un rapport de bug FreeRDP sur l'incohérence
  d'annonce série/parallèle (non déposé, pas de compte — même situation
  qu'en session 25 pour PyGObject, dont le rapport prêt à déposer dort
  dans `docs/pygobject-async-vfunc-userdata-bug-report.md`).

**Rejouer la suite complète (candidat n°1 de la session 29) est fait** :
le 2026-09-16 (session 30), unitaires + intégration xrdp réelle
+ GTK4 live sous Xvfb réunis dans un seul run pour la première fois
depuis le 2026-09-08 — 108 passed, 2 skipped, 2 xfailed, 0 failed,
aucune régression. Au passage, une dépendance de test manquante depuis
3 sessions (`Pillow`, jamais déclarée bien que déjà rencontrée le
2026-09-08) a été ajoutée à l'extra `gtk4-test`. Le conteneur de
développement a redémarré en cours de session (constat par `uptime`,
tout ce qui est sur disque a survécu, pas les processus en arrière-plan
ni `/dev/fuse`) — recette de démarrage combiné et robuste à ça
documentée dans `docs/test-environment.md`.

**Consolider le plugin RDP de GCM (candidat de la session 30) est
fait** le 2026-09-18 (session 31, voir « État courant ») — sous la
forme d'un visualiseur de référence autonome
(`gcm_gtk4_rdp_session.RdpSession` + `gcm_gtk4_demo_viewer.py`), GCM
lui-même restant absent de ce dépôt. Couvert en logique pure (+17
tests, aucune régression sur le reste) ; **et désormais aussi en
conditions réelles** — la revalidation live listée dans la version
précédente de cette section comme seul obstacle restant a été faite le
2026-09-18 (session suivante, voir « État courant »), avec deux bugs
réels trouvés et corrigés au passage (`install.sh --gtk4`, collision de
GType dans la suite de tests). Plus rien à rejouer côté GTK4.

Candidat suivant, une fois GCM lui-même disponible comme application
hôte dans ce dépôt : y assembler pour de vrai plutôt que dans le
visualiseur de référence — celui-ci est pensé comme point de départ
direct de ce travail (même assemblage RdpView + ClipboardBridge via
RdpSession), pas comme une fin en soi. D'ici là, plus aucun obstacle
d'environnement ne reste sur du code GTK4 déjà écrit — seuls USB et
imprimante/série/parallèle restent ouverts, tous deux bloqués par du
matériel/protocole externe plutôt que par du travail restant (voir
« État courant »). Une revérification complète menée le 2026-09-18
dans un nouveau bac à sable (session 33) n'a rien trouvé de nouveau à
corriger — 127 passed/1 skipped/2 xfailed/0 failed contre un vrai
serveur xrdp local, `install.sh --dev --gtk4` de bout en bout sans
intervention manuelle.

La consigne de fond reste inchangée : continuer les features à faire
sans attendre de décision sur les points bloqués par l'environnement
(voir `docs/sessions/session-23.md`).

## CI étendue (2026-10-11, issue #41)

Job « Intégration (GTK4 Xvfb, serveur RDP d'essai) » de `ci.yml`, non
bloquant. Il couvre les ponts GTK4 avec numpy, GTK4 live sur Xvfb (après
`build_gir.py`) et l'intégration live contre `freerdp-shadow-cli` (SAM via
`winpr-hash`, `ASYNCRDP_TEST_NO_DISP=1`). L'intégration asyncio/GLib passe
désormais par `integrations/gtk4/gcm_gtk4_asyncio.py` (gi.events, gbulb en
repli, gbulb étant cassé sous Python 3.14). Voir `docs/sessions/session-34.md`.

## Commandes de qualité

Développement de la bibliothèque cœur (venv isolé et reproductible via
`uv.lock`, ne couvre pas les extras `gtk4`/`usb` qui dépendent de paquets
système — voir plus bas) :

```bash
uv sync                                          # crée .venv/, installe cffi+loguru + ruff/pytest (groupe dev)
uv run ruff check .                              # lint — doit rester à 0 erreur
uv run pytest tests/ --ignore=tests/test_integration_live.py \
    --ignore=tests/test_gtk4_live.py --ignore=tests/test_gtk4_bridges.py \
    --ignore=tests/test_gtk4_clipboard_file_download.py \
    --ignore=tests/test_gtk4_demo_viewer.py
                                                   # unitaires, aucun serveur/GTK4 nécessaire
                                                   # (demo_viewer : doublures gtk_stub, mais numpy
                                                   #  requis par les modules sous test — extra gtk4)
```

Installation complète (système, apt + extras gtk4/usb — voir
« Points d'architecture », `uv pip install --system` y remplace `pip
install` depuis le 2026-09-15, comportement inchangé sinon) :

```bash
./install.sh --dev                              # + dépendances de test
pytest tests/                                    # unitaires (install système, pas de venv)

ASYNCRDP_TEST_HOST=<host> ASYNCRDP_TEST_USER=<user> ASYNCRDP_TEST_PASSWORD=<pass> \
    pytest tests/test_integration_live.py -v     # contre un vrai serveur RDP

ASYNCRDP_TEST_GTK4=1 LIBGL_ALWAYS_SOFTWARE=1 \
    pytest tests/test_gtk4_live.py -v            # GTK4 réel sous Xvfb (LIBGL_ALWAYS_SOFTWARE
                                                   # nécessaire, voir docs/test-environment.md)
```

## Points d'architecture à ne pas re-casser

- cffi en **mode API** (compilation réelle), jamais ABI/`dlopen` — les
  enums FreeRDP3 ne sont pas stables entre versions
- `freerdp_client_context_new()`, jamais `freerdp_new()` +
  `freerdp_context_new()` bruts — c'est LE bug qui a bloqué tous les
  canaux pendant une bonne partie du projet
- Toujours les constructeurs fournis (`freerdp_device_new()`...),
  jamais une struct reconstruite à la main même avec le bon layout de
  champs
- Redirections locales via `freerdp_client_load_addins()` — jamais de
  réimplémentation de rdpdr/urbdrc/rdpsnd
- Frames en `Queue(maxsize=1)` — jamais de backlog qui prend du retard
  sur l'affichage temps réel

Détail et post-mortems complets : `docs/architecture-and-lessons.md`.

## Pour aller plus loin

@docs/features-backlog.md

- `docs/architecture-and-lessons.md` — décisions d'architecture et
  bugs racines, en détail
- `docs/test-environment.md` — recette pour remonter un serveur de
  test (xrdp, PipeWire, GTK4/Xvfb) + pièges connus
- `docs/sessions/` — journal détaillé, une session par fichier ;
  index dans `docs/sessions/README.md`

## Instructions reçues dans le chat (issue #46)

Toute instruction reçue dans le chat (Perplexity Computer, Claude Code ou autre) devient une issue. Une phrase ne reste pas en l'air.

- Avant de commencer le travail, créer l'issue dans le dépôt concerné, en citant l'instruction mot pour mot avec sa date.
- Une demande qui touche plusieurs dépôts donne une issue par dépôt, chacune liée aux autres.
- Une question posée dans le chat sans réponse immédiate devient elle aussi une issue, ou un commentaire « Question pour Mathilde » sur l'issue existante.
- La PR ferme ou cite l'issue (`Closes #n`). Une instruction abandonnée est fermée avec sa raison (« not planned »).
- En fin de session, vérifier que chaque instruction du chat a son issue, ouverte ou fermée.
