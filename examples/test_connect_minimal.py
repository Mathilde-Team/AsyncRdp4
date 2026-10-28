#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_connect_minimal.py — premier test manuel, volontairement réduit au
strict nécessaire : connexion, quelques frames, déconnexion propre. Pas
de clipboard, pas de redirections (disques/imprimantes/USB/audio), pas de
GTK4 — l'objectif est d'isoler et valider la base (settings, fds/event
loop, GDI) avant d'ajouter la complexité des couches suivantes.

Ce que ce test valide s'il réussit :
  - la compilation du shim (settings, connect, GDI) est correcte
  - la boucle asyncio/fds ne bloque pas et ne fuit pas les descripteurs
  - au moins une frame arrive et a une taille cohérente

Ce qu'il NE valide PAS (volontairement, pour isoler les problèmes) :
  - clipboard, redirections, clavier/souris, resize — voir
    test_connect_full.py / test_full_suite.py pour ça.
"""

import argparse
import asyncio
import sys

from loguru import logger

import asyncrdp
from asyncrdp.tracing import traced


@traced
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host")
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument("--port", type=int, default=3389)
    p.add_argument("--domain", default=None, help="Domaine Active Directory (CORP\\utilisateur)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--frames", type=int, default=5, help="Nombre de frames à attendre avant succès")
    p.add_argument("--timeout", type=float, default=15.0, help="Délai max d'attente des frames (s)")
    p.add_argument(
        "--ignore-certificate",
        action="store_true",
        help="Ignore la vérification du certificat serveur (dev/lab uniquement, jamais en production)",
    )
    p.add_argument("--log-level", default="DEBUG", choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


@traced
async def main() -> int:
    args = parse_args()

    logger.remove()
    logger.add(sys.stderr, level=args.log_level)

    options = asyncrdp.RdpOptions(
        width=args.width,
        height=args.height,
        redirect_clipboard=False,
        redirect_printers=False,
        redirect_smartcards=False,
        redirect_drives=False,
        audio_playback=False,
        audio_capture=False,
        ignore_certificate=args.ignore_certificate,
    )

    logger.info("Connexion à {}:{} ({})...", args.host, args.port, args.username)

    frame_count = 0
    try:
        async with asyncrdp.connect(
            args.host, args.port, args.username, args.password, domain=args.domain, options=options
        ) as client:
            logger.info("Connecté. Attente de {} frame(s)...", args.frames)

            @traced
            async def count_frames():
                nonlocal frame_count
                while frame_count < args.frames:
                    await client.get_frame()
                    frame_count += 1
                    w, h = client.frame_size or (0, 0)
                    logger.info("Frame {}/{} reçue : {}x{}", frame_count, args.frames, w, h)

            try:
                await asyncio.wait_for(count_frames(), timeout=args.timeout)
            except asyncio.TimeoutError:
                logger.warning("Timeout en attendant les frames ({} reçues sur {})", frame_count, args.frames)

            logger.info("Test terminé, déconnexion...")
    except asyncrdp.FreeRDPError as exc:
        logger.error("Échec de connexion : {}", exc)
        return 1

    if frame_count == 0:
        logger.error("ÉCHEC : aucune frame reçue — GDI/EndPaint probablement en cause")
        return 1

    logger.success("SUCCÈS : {} frame(s) reçue(s), connexion et déconnexion propres", frame_count)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
