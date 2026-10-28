# SPDX-License-Identifier: GPL-3.0-or-later
"""
asyncrdp — binding asyncio sur libfreerdp3 via cffi (mode API).

    import asyncio
    import asyncrdp

    async def main():
        async with asyncrdp.connect("192.168.1.10", username="alice", password="secret") as client:
            frame = await client.get_frame()

    asyncio.run(main())

Voir README.md et CLAUDE.md à la racine du dépôt pour la documentation
complète (état des fonctionnalités, architecture, historique des bugs
trouvés en testant contre un vrai serveur).
"""

from ._core import (
    Client,
    Clipboard,
    DriveMapping,
    FreeRDPError,
    GatewayOptions,
    Keyboard,
    MonitorDef,
    Mouse,
    ParallelMapping,
    PrinterMapping,
    RdpOptions,
    RemoteAppOptions,
    RemoteFileInfo,
    SerialMapping,
    connect,
)

__version__ = "0.1.0"

__all__ = [
    "Client",
    "Clipboard",
    "DriveMapping",
    "FreeRDPError",
    "GatewayOptions",
    "Keyboard",
    "MonitorDef",
    "Mouse",
    "ParallelMapping",
    "PrinterMapping",
    "RdpOptions",
    "RemoteAppOptions",
    "RemoteFileInfo",
    "SerialMapping",
    "connect",
]
