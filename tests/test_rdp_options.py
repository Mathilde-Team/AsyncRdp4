# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests unitaires des dataclasses de configuration — construction, valeurs
par défaut, pas d'appel réseau. RdpOptions._apply() (qui parle au shim C)
est testée sur un vrai contexte FreeRDP non connecté dans
test_core_contexte.py (issue #50).
"""

import asyncrdp


def test_rdp_options_defaults():
    opts = asyncrdp.RdpOptions()
    assert opts.width == 1920
    assert opts.height == 1080
    assert opts.color_depth == 32
    assert opts.enable_nla is True
    assert opts.redirect_clipboard is True
    assert opts.redirect_drives is False
    assert opts.drives == []
    assert opts.monitors == []
    assert opts.gateway is None
    assert opts.remoteapp is None
    assert opts.keyboard_layout is None
    assert opts.enable_graphics_pipeline is False


def test_rdp_options_override():
    opts = asyncrdp.RdpOptions(width=1280, height=800, redirect_clipboard=False)
    assert opts.width == 1280
    assert opts.height == 800
    assert opts.redirect_clipboard is False
    # Les autres champs gardent leurs défauts.
    assert opts.color_depth == 32


def test_drive_mapping():
    d = asyncrdp.DriveMapping("home", "/home/alice")
    assert d.name == "home"
    assert d.path == "/home/alice"


def test_rdp_options_with_drives():
    opts = asyncrdp.RdpOptions(
        redirect_drives=True,
        drives=[
            asyncrdp.DriveMapping("home", "/home/alice"),
            asyncrdp.DriveMapping("docs", "/home/alice/Documents"),
        ],
    )
    assert len(opts.drives) == 2
    assert opts.drives[0].name == "home"
    assert opts.drives[1].path == "/home/alice/Documents"


def test_printer_mapping_defaults():
    p = asyncrdp.PrinterMapping("MonImprimante")
    assert p.name == "MonImprimante"
    assert p.driver is None
    assert p.is_default is False


def test_serial_and_parallel_mapping():
    s = asyncrdp.SerialMapping("COM1", "/dev/ttyUSB0")
    assert s.name == "COM1"
    assert s.path == "/dev/ttyUSB0"

    p = asyncrdp.ParallelMapping("LPT1", "/dev/usb/lp0")
    assert p.name == "LPT1"


def test_serial_and_parallel_document_the_freerdp_announce_limitation():
    """
    Régression de documentation, pas de comportement.

    La limitation trouvée en conditions réelles le 2026-09-16 (le canal
    client `serial`/`parallel` de FreeRDP remplit DeviceData, que le canal
    serveur de FreeRDP refuse — voir docs/sessions/session-29.md) n'est pas
    détectable à l'exécution côté asyncrdp : elle se manifeste uniquement
    dans la réponse du serveur, hors de portée de l'API. Le seul garde-fou
    possible est donc que l'avertissement reste attaché aux deux
    dataclasses concernées, là où un appelant le lira.
    """
    for cls in (asyncrdp.SerialMapping, asyncrdp.ParallelMapping):
        doc = cls.__doc__ or ""
        assert "DeviceData" in doc, f"{cls.__name__} ne documente plus la limitation d'annonce"
        assert "session-29" in doc, f"{cls.__name__} ne renvoie plus vers la session qui l'a constatée"


def test_probe_rdpdr_options_announce_the_four_device_types():
    """
    La sonde `examples/probe_rdpdr_devices.py` ne vaut que si elle annonce
    bien les trois types en question PLUS un disque témoin : sans le
    témoin, un serveur muet et un serveur qui refuse tout sont
    indiscernables. On fige donc cette composition.
    """
    import importlib.util
    import pathlib

    probe_path = pathlib.Path(__file__).resolve().parent.parent / "examples" / "probe_rdpdr_devices.py"
    spec = importlib.util.spec_from_file_location("probe_rdpdr_devices", probe_path)
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    opts = probe.build_probe_options("/dev/ttyS0", "/dev/lp0", "/tmp")

    assert [p.name for p in opts.printers] == ["PRN1"]
    assert [s.name for s in opts.serial_ports] == ["COM3"]
    assert [p.name for p in opts.parallel_ports] == ["LPT1"]
    # Le témoin positif : un type que même xrdp accepte.
    assert [d.name for d in opts.drives] == ["probe"]
    assert opts.redirect_drives is True
    # Annoncer UNE imprimante nommée, pas toutes les files locales.
    assert opts.redirect_printers is False


def test_monitor_def():
    m = asyncrdp.MonitorDef(0, 0, 1920, 1080, is_primary=True)
    assert (m.x, m.y, m.width, m.height) == (0, 0, 1920, 1080)
    assert m.is_primary is True

    m2 = asyncrdp.MonitorDef(1920, 0, 1280, 1024)
    assert m2.is_primary is False  # défaut


def test_rdp_options_multimonitor_bounding_box_inputs():
    """Vérifie seulement que les MonitorDef sont bien stockés tels quels —
    le calcul de la bounding box (largeur/hauteur totales) se fait dans
    RdpOptions._apply(), testé sur un vrai contexte FreeRDP dans
    test_core_contexte.py."""
    opts = asyncrdp.RdpOptions(
        monitors=[
            asyncrdp.MonitorDef(0, 0, 1280, 800, is_primary=True),
            asyncrdp.MonitorDef(1280, 0, 1024, 768),
        ]
    )
    assert len(opts.monitors) == 2
    total_width = max(m.x + m.width for m in opts.monitors)
    total_height = max(m.y + m.height for m in opts.monitors)
    assert (total_width, total_height) == (2304, 800)


def test_gateway_options_defaults():
    gw = asyncrdp.GatewayOptions("gw.example.com")
    assert gw.hostname == "gw.example.com"
    assert gw.port == 443
    assert gw.username is None
    assert gw.usage_method == 1


def test_remoteapp_options():
    ra = asyncrdp.RemoteAppOptions("||notepad", name="Notepad")
    assert ra.program == "||notepad"
    assert ra.name == "Notepad"
    assert ra.cmdline is None


def test_rdp_options_gateway_and_remoteapp_together():
    opts = asyncrdp.RdpOptions(
        gateway=asyncrdp.GatewayOptions("gw.corp.local", username="alice", password="secret"),
        remoteapp=asyncrdp.RemoteAppOptions("||mstsc"),
    )
    assert opts.gateway.hostname == "gw.corp.local"
    assert opts.remoteapp.program == "||mstsc"


def test_freerdp_error_is_exception():
    assert issubclass(asyncrdp.FreeRDPError, Exception)
    err = asyncrdp.FreeRDPError("[123] test")
    assert str(err) == "[123] test"
