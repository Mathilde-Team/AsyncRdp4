#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
probe_rdpdr_devices.py — annonce une imprimante, un port série, un port
parallèle et un disque à un serveur RDP, puis se déconnecte.

Sert à observer ce que le serveur RÉPOND à chaque type de périphérique
(STATUS_SUCCESS vs STATUS_NOT_SUPPORTED / ERROR_INVALID_DATA), question
ouverte depuis le 2026-09-05 dans `docs/features-backlog.md`. Le disque
sert de témoin positif : c'est le seul type que xrdp accepte, donc s'il
ressort différemment des trois autres, la comparaison est parlante.

Ce script ne lit RIEN de la réponse lui-même : la réponse par
périphérique (PAKID_CORE_DEVICE_REPLY) est traitée à l'intérieur du canal
`rdpdr` de FreeRDP, pas remontée à l'API asyncrdp. Il faut donc l'observer
du côté serveur — soit dans les logs d'un vrai serveur, soit avec le
harnais décrit dans `docs/sessions/session-29.md` (serveur d'exemple
FreeRDP + canal serveur RDPDR câblé, qui journalise chaque annonce et
chaque réponse).

Usage :
    python3 examples/probe_rdpdr_devices.py --host 127.0.0.1 --port 13389

Résultat obtenu le 2026-09-16 contre ce harnais (FreeRDP 3.31.0) :
    imprimante  -> STATUS_SUCCESS
    disque      -> STATUS_SUCCESS
    série       -> pas de callback, ERROR_INVALID_DATA (DeviceDataLength != 0)
    parallèle   -> idem
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
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3389)
    p.add_argument("--username", default="probe")
    p.add_argument("--password", default="probe")
    p.add_argument("--domain", default=None)
    p.add_argument("--serial-path", default="/dev/ttyS0")
    p.add_argument("--parallel-path", default="/dev/lp0")
    p.add_argument("--drive-path", default="/tmp", help="Témoin positif (type accepté partout)")
    p.add_argument(
        "--settle",
        type=float,
        default=6.0,
        help="Secondes d'attente après connexion, pour laisser l'échange RDPDR aboutir",
    )
    p.add_argument("--log-level", default="INFO", choices=["TRACE", "DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def build_probe_options(serial_path: str, parallel_path: str, drive_path: str) -> "asyncrdp.RdpOptions":
    """
    Construit la configuration exacte de la sonde.

    Extrait de main() pour être testable sans serveur (voir
    tests/test_rdp_options.py) : la valeur de ce script tient à ce qu'il
    annonce précisément les trois types en question PLUS un disque témoin,
    donc cette composition mérite une régression.
    """
    return asyncrdp.RdpOptions(
        width=1024,
        height=768,
        redirect_clipboard=False,
        redirect_smartcards=False,
        audio_playback=False,
        audio_capture=False,
        ignore_certificate=True,
        # redirect_printers=False + une entrée explicite : on annonce UNE
        # imprimante nommée, pas toutes les files locales de la machine.
        redirect_printers=False,
        printers=[asyncrdp.PrinterMapping(name="PRN1", driver="Generic / Text Only")],
        serial_ports=[asyncrdp.SerialMapping(name="COM3", path=serial_path)],
        parallel_ports=[asyncrdp.ParallelMapping(name="LPT1", path=parallel_path)],
        redirect_drives=True,
        drives=[asyncrdp.DriveMapping(name="probe", path=drive_path)],
    )


@traced
async def main() -> int:
    args = parse_args()

    logger.remove()
    logger.add(sys.stderr, level=args.log_level)

    options = build_probe_options(args.serial_path, args.parallel_path, args.drive_path)

    try:
        async with asyncrdp.connect(
            args.host,
            port=args.port,
            username=args.username,
            password=args.password,
            domain=args.domain,
            options=options,
        ):
            print("PROBE|CONNECTED")
            await asyncio.sleep(args.settle)
    except Exception as exc:  # script de diagnostic : on veut voir l'erreur brute, quelle qu'elle soit
        print(f"PROBE|CONNECT_FAILED|{exc!r}")
        logger.debug("connect() a échoué : {}", exc)
        return 1

    print("PROBE|DISCONNECTED")
    print("Réponses par périphérique : à lire côté serveur (voir docstring).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
