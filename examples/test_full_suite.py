#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_full_suite.py — script de test complet, consolidant toute la campagne
de validation menée contre un vrai serveur RDP (xrdp) : connexion de base,
input, resize, clipboard (texte/image/fichiers, les deux sens), disque
redirigé (lecture/écriture réelles), multi-écran.

Deux catégories de tests :
  - AUTONOMES : ne dépendent que du serveur RDP cible, tournent partout
    (frames, input, resize, clipboard "announce" client -> serveur, resize,
    multi-écran).
  - DÉPENDANTS DE L'ENVIRONNEMENT : nécessitent un accès shell à la session
    distante pour y déposer du contenu (clipboard "pull" serveur -> client
    via xclip, disque redirigé via écriture dans le point de montage). Ces
    tests sont clairement isolés et se désactivent proprement si les
    prérequis (xclip, DISPLAY de la session, chemin de montage) ne sont
    pas fournis — ils documentent COMMENT on a validé ces chemins, pas une
    garantie qu'ils fonctionnent identiquement sur toute autre machine.

Usage :
    # Tests autonomes seulement (fonctionne contre n'importe quel serveur RDP) :
    python test_full_suite.py <host> [port] <username> <password>

    # Avec les tests dépendants de l'environnement (nécessite un accès
    # shell root à la session, comme dans le sandbox de développement) :
    python test_full_suite.py <host> [port] <username> <password> \\
        --session-display :11 --session-user rdptest \\
        --drive-mount-parent /home/rdptest/thinclient_drives
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import subprocess
import sys
from pathlib import Path

from loguru import logger

import asyncrdp
from asyncrdp.tracing import traced


@traced
def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("port", nargs="?", type=int, default=3389)
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument(
        "--session-display",
        default=None,
        help="DISPLAY X11 de la session distante (ex: :11), pour les tests clipboard "
        "'pull' via xclip. Nécessite un accès shell à la machine hébergeant la "
        "session (typique en dev/sandbox, pas en usage normal).",
    )
    p.add_argument(
        "--session-user",
        default=None,
        help="Utilisateur système propriétaire de la session (pour lancer xclip en son nom).",
    )
    p.add_argument(
        "--drive-mount-parent",
        default=None,
        help="Dossier PARENT (pas le point de montage lui-même) où xrdp-chansrv "
        "fait apparaître les disques redirigés (ex: /home/rdptest/thinclient_drives). "
        "Le nom exact du sous-dossier monté n'est pas prévisible à l'avance : "
        "xrdp/FreeRDP peut tronquer le libellé du disque (constaté à 8 caractères, "
        "voir CLAUDE.md) — ce script le découvre dynamiquement en comparant le "
        "contenu de ce dossier avant/après connexion, plutôt que de le supposer. "
        "Nécessite --session-user : ce dossier est peuplé par xrdp-chansrv en tant "
        "que cet utilisateur (FUSE sans allow_other), un accès direct depuis un "
        "autre utilisateur (root compris) échoue avec Permission denied.",
    )
    return p.parse_args()


class Results:
    @traced
    def __init__(self):
        self.results: dict[str, bool | None] = {}  # None = ignoré (prérequis absents)

    @traced
    def record(self, name: str, ok: bool) -> None:
        self.results[name] = ok
        logger.info("[{}] {}", "OK  " if ok else "ÉCHEC", name)

    @traced
    def skip(self, name: str, reason: str) -> None:
        self.results[name] = None
        logger.info("[SKIP] {} ({})", name, reason)

    @traced
    def summary(self) -> int:
        logger.info("=" * 60)
        logger.info("RÉSUMÉ")
        for name, ok in self.results.items():
            label = "OK" if ok else ("SKIP" if ok is None else "ÉCHEC")
            logger.info("  {:8s} {}", label, name)
        failed = [n for n, ok in self.results.items() if ok is False]
        return 1 if failed else 0


# ---------------------------------------------------------------------------
# Tests autonomes
# ---------------------------------------------------------------------------


@traced
async def test_frames(client: asyncrdp.Client, results: Results) -> None:
    try:
        await asyncio.wait_for(client.get_frame(), timeout=10.0)
        results.record("frames", True)
    except asyncio.TimeoutError as exc:
        logger.debug("délai dépassé sur les frames : {}", exc)
        results.record("frames", False)


@traced
async def test_input(client: asyncrdp.Client, results: Results) -> None:
    try:
        client.mouse.move(100, 100)
        client.mouse.click("left")
        client.keyboard.write("test")
        client.keyboard.key_press("return")
        results.record("clavier/souris", True)
    except Exception as exc:
        logger.error("Clavier/souris : {}", exc)
        results.record("clavier/souris", False)


@traced
async def test_resize(client: asyncrdp.Client, results: Results) -> None:
    try:
        ok = client.request_resize(1024, 640)
        await asyncio.sleep(2.0)
        try:
            await asyncio.wait_for(client.get_frame(), timeout=5.0)
            got_frame = True
        except asyncio.TimeoutError as exc:
            logger.debug("délai dépassé sur le resize dynamique : {}", exc)
            got_frame = False
        results.record("resize dynamique", ok and got_frame)
    except Exception as exc:
        logger.error("Resize : {}", exc)
        results.record("resize dynamique", False)


@traced
async def test_clipboard_announce(client: asyncrdp.Client, results: Results) -> None:
    try:
        client.clipboard.get_local_text = lambda: "asyncrdp test clipboard"
        client.clipboard.announce_local_text("asyncrdp test clipboard")
        await asyncio.sleep(1.0)
        results.record("clipboard announce (client -> serveur)", True)
    except Exception as exc:
        logger.error("Clipboard announce : {}", exc)
        results.record("clipboard announce (client -> serveur)", False)


@traced
async def test_clipboard_request(client: asyncrdp.Client, results: Results) -> None:
    try:
        text = await asyncio.wait_for(client.clipboard.request_remote_text(), timeout=5.0)
        results.record("clipboard request (protocole)", True)
        logger.debug("Texte distant (peut être vide si rien n'est dans le presse-papier serveur) : {!r}", text)
    except Exception as exc:
        logger.warning("Clipboard request : {}", exc)
        results.record("clipboard request (protocole)", False)


@traced
async def test_multimonitor(host, port, username, password, results: Results) -> None:
    """Connexion séparée : la résolution multi-écran se règle à la
    connexion, pas modifiable en cours de session comme un simple resize."""
    try:
        options = asyncrdp.RdpOptions(
            ignore_certificate=True,
            monitors=[
                asyncrdp.MonitorDef(0, 0, 1280, 800, is_primary=True),
                asyncrdp.MonitorDef(1280, 0, 1024, 768, is_primary=False),
            ],
        )
        async with asyncrdp.connect(host, port, username, password, options=options) as client:
            raw = await asyncio.wait_for(client.get_frame(), timeout=10.0)
            w, h = client.frame_size
            expected = w == 2304 and h == 800 and len(raw) == w * h * 4
            results.record(f"multi-écran (frame {w}x{h}, {len(raw)} octets)", expected)
    except Exception as exc:
        logger.error("Multi-écran : {}", exc)
        results.record("multi-écran", False)


# ---------------------------------------------------------------------------
# Tests dépendants de l'environnement (session shell accessible)
# ---------------------------------------------------------------------------


@traced
async def test_clipboard_pull_text(client: asyncrdp.Client, results: Results, args) -> None:
    if not args.session_display or not args.session_user:
        results.skip("clipboard pull texte (serveur -> client)", "--session-display/--session-user non fournis")
        return

    received = asyncio.Event()
    received_text = None

    @traced
    def on_text(text):
        nonlocal received_text
        received_text = text
        received.set()

    client.clipboard.on_remote_text_changed = on_text
    expected = "Contenu de test asyncrdp full_suite"
    tmp_file = Path("/tmp/asyncrdp_test_clip.txt")
    tmp_file.write_text(expected)

    subprocess.Popen(
        [
            "su",
            args.session_user,
            "-c",
            f"DISPLAY={args.session_display} setsid xclip -selection clipboard -i {tmp_file}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        await asyncio.wait_for(received.wait(), timeout=10.0)
        results.record("clipboard pull texte (serveur -> client)", received_text == expected)
    except asyncio.TimeoutError as exc:
        logger.debug("délai dépassé sur le pull texte du presse-papiers : {}", exc)
        results.record("clipboard pull texte (serveur -> client)", False)
    finally:
        client.clipboard.on_remote_text_changed = None


@traced
async def test_clipboard_pull_files(client: asyncrdp.Client, results: Results, args) -> None:
    if not args.session_display or not args.session_user:
        results.skip("clipboard pull fichiers (serveur -> client)", "--session-display/--session-user non fournis")
        return

    received = asyncio.Event()
    received_files = None

    @traced
    def on_files(files):
        nonlocal received_files
        received_files = files
        received.set()

    client.clipboard.on_remote_files_changed = on_files
    expected_content = b"contenu de test pour transfert de fichier RDP"
    test_file = Path("/tmp/asyncrdp_test_file.txt")
    test_file.write_bytes(expected_content)
    uri_list = Path("/tmp/asyncrdp_test_uri_list.txt")
    uri_list.write_text(f"file://{test_file}\n")

    subprocess.Popen(
        [
            "su",
            args.session_user,
            "-c",
            f"DISPLAY={args.session_display} setsid xclip -selection clipboard -t text/uri-list -i {uri_list}",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        await asyncio.wait_for(received.wait(), timeout=10.0)
        if not received_files:
            results.record("clipboard pull fichiers (serveur -> client)", False)
            return
        data = await client.clipboard.request_remote_file_contents(0)
        results.record("clipboard pull fichiers (serveur -> client)", data == expected_content)
    except asyncio.TimeoutError as exc:
        logger.debug("délai dépassé sur le pull fichiers du presse-papiers : {}", exc)
        results.record("clipboard pull fichiers (serveur -> client)", False)
    finally:
        client.clipboard.on_remote_files_changed = None


@traced
async def _remote_run(args, shell_cmd: str, timeout: float = 5.0) -> subprocess.CompletedProcess | None:
    """Exécute une commande shell en tant que l'utilisateur propriétaire de
    la session distante (args.session_user), plutôt qu'en root (ou tout
    autre utilisateur ayant lancé ce script). Nécessaire pour accéder au
    point de montage FUSE du disque redirigé : xrdp-chansrv le monte en
    tant que cet utilisateur, sans l'option allow_other — un accès direct
    depuis un utilisateur différent échoue avec Permission denied, même
    en root (constaté le 2026-09-03, voir CLAUDE.md : ce n'est pas
    root qui accède au FUSE d'un autre utilisateur qui pose problème en
    général, mais FUSE qui restreint spécifiquement à l'utilisateur
    monteur par défaut).

    ASYNC PAR NÉCESSITÉ, PAS PAR STYLE (constaté le 2026-09-04) : lire un
    fichier DANS le montage FUSE (par opposition à simplement lister le
    dossier parent, qui est une opération normale sur un dossier non-FUSE)
    oblige chansrv à faire un aller-retour RDPDR vers CE client asyncrdp
    pour aller chercher le contenu réel du fichier local exposé. Si cet
    appel est fait de façon synchrone/bloquante depuis la coroutine qui
    tient la connexion, la boucle asyncio est gelée pendant l'appel — donc
    plus rien ne traite les fds enregistrés par asyncrdp, donc la requête
    RDPDR de chansrv ne reçoit jamais de réponse, donc `cat`/`test -e`
    bloque indéfiniment. Interblocage circulaire pur et simple : la
    commande distante attend la boucle asyncio, qui attend la commande
    distante. D'où `asyncio.to_thread` ici plutôt qu'un simple
    `subprocess.run` — le thread séparé libère la boucle asyncio pendant
    l'attente, qui peut alors continuer à servir la connexion. Le timeout
    reste en place par sécurité, mais ne devrait plus jamais se déclencher
    une fois la vraie cause corrigée."""

    @traced
    def _blocking() -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["su", args.session_user, "-c", shell_cmd],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            logger.debug("délai dépassé sur la sortie du sous-processus : {}", exc)
            return None

    result = await asyncio.to_thread(_blocking)
    if result is None:
        logger.warning("commande distante bloquée au-delà de {}s, abandon : {}", timeout, shell_cmd)
    return result


@traced
async def _remote_listdir(args, path: Path) -> set[str] | None:
    result = await _remote_run(args, f"ls -1a {shlex.quote(str(path))}")
    if result is None or result.returncode != 0:
        return None
    return {name for name in result.stdout.decode("utf-8", errors="replace").splitlines() if name not in (".", "..")}


@traced
async def _remote_exists(args, path: Path) -> bool:
    result = await _remote_run(args, f"test -e {shlex.quote(str(path))}")
    return result is not None and result.returncode == 0


@traced
async def _remote_read_text(args, path: Path) -> str | None:
    result = await _remote_run(args, f"cat {shlex.quote(str(path))}")
    if result is None or result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


@traced
async def _remote_write_text(args, path: Path, content: str) -> bool:
    def _blocking() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["su", args.session_user, "-c", f"cat > {shlex.quote(str(path))}"],
            input=content.encode("utf-8"),
            capture_output=True,
            timeout=5.0,
        )

    try:
        result = await asyncio.to_thread(_blocking)
    except subprocess.TimeoutExpired:
        logger.warning("écriture distante bloquée au-delà de 5s, abandon : {}", path)
        return False
    return result.returncode == 0


@traced
async def test_drive_redirection(host, port, username, password, results: Results, args) -> None:
    """
    NOTE DE FIABILITÉ (mise à jour 2026-09-03) : deux causes réelles et
    distinctes ont été trouvées à la fragilité historique de ce test,
    attribuée jusqu'ici (sans certitude) à la réutilisation du cycle de
    vie de la session X. Diagnostic complet dans CLAUDE.md, résumé ici :

    1. Le point de montage FUSE du disque redirigé est créé par
       xrdp-chansrv en tant que --session-user, sans l'option
       allow_other. Un accès direct au `Path` depuis un utilisateur
       différent (root compris) échoue avec Permission denied — corrigé
       en passant par `su <session-user> -c ...` (voir `_remote_run`
       ci-dessus).
    2. Le NOM RÉEL du point de montage n'est pas forcément celui demandé
       via le libellé du DriveMapping : xrdp/FreeRDP tronque ce libellé
       (constaté à 8 caractères, cohérent avec le champ PreferredDosName
       de MS-RDPEFS) — "testdrive" (9 caractères) devient "testdriv" sur
       le disque, silencieusement, sans erreur ni log qui l'indique
       clairement. Deviner le nom tronqué à l'avance est fragile ; ce
       test découvre maintenant le nom réel dynamiquement (comparaison
       du contenu de --drive-mount-parent avant/après connexion) plutôt
       que de le supposer.

    Une sensibilité résiduelle au cycle de vie de session au-delà de ces
    deux causes n'a pas été formellement exclue mais n'a plus été
    observée une fois ces deux corrections en place (voir CLAUDE.md pour
    le détail des essais : session fraîche et session réutilisée se sont
    comportées de façon identique une fois ces deux bugs corrigés).
    """
    if not args.drive_mount_parent:
        results.skip("disque redirigé (lecture + écriture réelles)", "--drive-mount-parent non fourni")
        return
    if not args.session_user:
        results.skip(
            "disque redirigé (lecture + écriture réelles)",
            "--session-user non fourni (requis pour accéder au montage FUSE distant, "
            "voir l'aide de --drive-mount-parent)",
        )
        return

    mount_parent = Path(args.drive_mount_parent)
    local_dir = mount_parent.parent / "asyncrdp_drive_test_dir"  # dossier réel exposé comme lecteur
    local_dir.mkdir(exist_ok=True)
    marker_out = local_dir / "from_client.txt"
    marker_out.write_text("écrit par le client, avant connexion")

    drive_label = "asyncrdptest"  # le nom EFFECTIF sur le disque peut être tronqué, voir docstring
    before = await _remote_listdir(args, mount_parent) or set()

    try:
        options = asyncrdp.RdpOptions(
            ignore_certificate=True,
            redirect_drives=True,
            drives=[asyncrdp.DriveMapping(drive_label, str(local_dir))],
        )
        async with asyncrdp.connect(host, port, username, password, options=options) as _client:
            await asyncio.sleep(6.0)  # laisse le temps au montage FUSE de se faire côté session

            mount = None
            for _ in range(20):
                after = await _remote_listdir(args, mount_parent)
                if after is not None:
                    new_entries = after - before - {".clipboard"}
                    # tolère la présence d'autres montages déjà là (ex. "transcripts",
                    # auto-redirigé par ce sandbox) : on ne garde que celui qui
                    # commence par le même préfixe que le libellé demandé, tronqué
                    # ou non.
                    candidates = [e for e in new_entries if drive_label.startswith(e) or e.startswith(drive_label)]
                    if candidates:
                        mount = mount_parent / candidates[0]
                        break
                await asyncio.sleep(1.0)

            if mount is None:
                logger.warning(
                    "disque redirigé : point de montage introuvable sous {} après 20s (entrées vues : {})",
                    mount_parent,
                    after if after is not None else "?",
                )
                results.record("disque redirigé : lecture client -> serveur", False)
                results.record("disque redirigé : écriture serveur -> client", False)
                return

            logger.info("disque redirigé : point de montage réel détecté = {} (demandé : {!r})", mount, drive_label)

            read_ok = False
            remote_marker = mount / "from_client.txt"
            for _ in range(20):
                if await _remote_exists(args, remote_marker):
                    content = await _remote_read_text(args, remote_marker)
                    if content is not None and content.startswith("écrit par le client"):
                        read_ok = True
                        break
                await asyncio.sleep(1.0)

            write_ok = False
            if read_ok:
                await _remote_write_text(args, mount / "from_server.txt", "écrit depuis la session distante")
                await asyncio.sleep(1.0)
                marker_in = local_dir / "from_server.txt"
                write_ok = marker_in.exists() and "écrit depuis la session distante" in marker_in.read_text()

            results.record("disque redirigé : lecture client -> serveur", read_ok)
            results.record("disque redirigé : écriture serveur -> client", write_ok)
    except Exception as exc:
        logger.error("Disque redirigé : {}", exc)
        results.record("disque redirigé", False)


@traced
async def main() -> int:
    args = parse_args()
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    results = Results()
    options = asyncrdp.RdpOptions(width=1280, height=800, redirect_clipboard=True, ignore_certificate=True)

    logger.info("== Suite de tests autonomes ==")
    async with asyncrdp.connect(args.host, args.port, args.username, args.password, options=options) as client:
        await test_frames(client, results)
        await test_input(client, results)
        await test_resize(client, results)
        await test_clipboard_announce(client, results)
        await test_clipboard_request(client, results)

        logger.info("== Tests dépendants de l'environnement ==")
        await test_clipboard_pull_text(client, results, args)
        await test_clipboard_pull_files(client, results, args)

    logger.info("== Multi-écran (connexion séparée) ==")
    await test_multimonitor(args.host, args.port, args.username, args.password, results)

    logger.info("== Disque redirigé (connexion séparée) ==")
    await test_drive_redirection(args.host, args.port, args.username, args.password, results, args)

    return results.summary()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
