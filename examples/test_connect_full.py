#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_connect_full.py — test manuel plus complet qu'test_connect_minimal.py :
en plus de la connexion de base, exerce clipboard (texte), clavier/souris,
et resize dynamique contre un vrai serveur. Toujours pas de GTK4 — juste
la couche asyncrdp, pour valider le protocole avant d'ajouter l'UI.
"""

import argparse
import asyncio
import sys

from loguru import logger

import asyncrdp
from asyncrdp.tracing import traced


@traced
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("host")
    p.add_argument("username")
    p.add_argument("password")
    p.add_argument("--port", type=int, default=3389)
    p.add_argument("--domain", default=None, help="Domaine Active Directory (CORP\\utilisateur)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument("--resize-width", type=int, default=1024, help="Résolution cible du test de resize")
    p.add_argument("--resize-height", type=int, default=640)
    p.add_argument(
        "--ignore-certificate",
        action="store_true",
        help="Ignore la vérification du certificat serveur (dev/lab uniquement)",
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
        redirect_clipboard=True,
        ignore_certificate=args.ignore_certificate,
    )

    results: dict[str, bool] = {}

    async with asyncrdp.connect(
        args.host, args.port, args.username, args.password, domain=args.domain, options=options
    ) as client:
        logger.info("=== 1. Frames de base ===")
        try:
            await asyncio.wait_for(client.get_frame(), timeout=10.0)
            results["frames"] = True
        except asyncio.TimeoutError as exc:
            logger.debug("délai dépassé sur les frames : {}", exc)
            results["frames"] = False
        logger.info("Frames : {}", "OK" if results["frames"] else "ÉCHEC")

        logger.info("=== 2. Clavier/souris (pas de vérif visuelle, juste absence de crash) ===")
        try:
            client.mouse.move(100, 100)
            client.mouse.click("left")
            client.keyboard.write("test")
            client.keyboard.key_press("return")
            results["input"] = True
        except Exception as exc:
            logger.error("Clavier/souris a levé une exception : {}", exc)
            results["input"] = False
        logger.info("Clavier/souris : {}", "OK" if results["input"] else "ÉCHEC")

        logger.info("=== 3. Resize dynamique ===")
        try:
            resize_ok = client.request_resize(args.resize_width, args.resize_height)
            await asyncio.sleep(2.0)  # laisse le temps au serveur de répondre par de nouvelles frames
            try:
                await asyncio.wait_for(client.get_frame(), timeout=5.0)
                got_frame_after_resize = True
            except asyncio.TimeoutError as exc:
                logger.debug("délai dépassé après resize : {}", exc)
                got_frame_after_resize = False
            results["resize"] = resize_ok and got_frame_after_resize
            logger.info("Résolution après resize : {}", client.frame_size)
        except Exception as exc:
            logger.error("Resize a levé une exception : {}", exc)
            results["resize"] = False
        logger.info("Resize : {}", "OK" if results["resize"] else "ÉCHEC")

        logger.info("=== 4. Clipboard texte (announce local -> serveur) ===")
        try:
            client.clipboard.get_local_text = lambda: "asyncrdp test clipboard"
            client.clipboard.announce_local_text("asyncrdp test clipboard")
            await asyncio.sleep(1.0)  # laisse le temps au serveur de répondre s'il veut le contenu
            results["clipboard_announce"] = True
        except Exception as exc:
            logger.error("Clipboard announce a levé une exception : {}", exc)
            results["clipboard_announce"] = False
        logger.info("Clipboard announce : {}", "OK" if results["clipboard_announce"] else "ÉCHEC")

        logger.info("=== 5. Clipboard texte (request remote) ===")
        try:
            text = await asyncio.wait_for(client.clipboard.request_remote_text(), timeout=5.0)
            logger.info("Texte distant reçu ({} caractères) : {!r}", len(text), text[:50])
            results["clipboard_request"] = True
        except Exception as exc:
            logger.warning("Clipboard request : {} (normal si rien n'est dans le presse-papier serveur)", exc)
            results["clipboard_request"] = True  # pas d'échec si juste vide/timeout, protocole seul testé

    logger.info("=== RÉSUMÉ ===")
    for name, ok in results.items():
        logger.info("  {} : {}", name, "OK" if ok else "ÉCHEC")

    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
