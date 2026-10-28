# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests de `_core.py` sur un vrai contexte FreeRDP, sans serveur (issue #50).

`asyncrdp_context_new()` crée un vrai `rdpContext` (libfreerdp3) sans se
connecter : `RdpOptions._apply()` y écrit donc de vrais `rdpSettings`
(lecteurs, imprimantes, ports, USB, multi-écran, passerelle, RemoteApp,
pipeline graphique…). Les chemins d'échec, que FreeRDP ne produit pas sur
des réglages valides, passent par une injection de fautes ciblée : seule la
fonction nommée renvoie 0, toutes les autres restent celles du vrai shim.

`connect()` vers un port local fermé exerce la vraie remontée d'erreur
FreeRDP (`_raise_last_error`) sans serveur RDP. Le chemin connecté reste
couvert par `test_integration_live.py` (job integration).
"""

from __future__ import annotations

import socket

import pytest

import asyncrdp
from asyncrdp import _core
from asyncrdp._core import (
    DriveMapping,
    FreeRDPError,
    GatewayOptions,
    MonitorDef,
    ParallelMapping,
    PrinterMapping,
    RdpOptions,
    RemoteAppOptions,
    SerialMapping,
    ffi,
)

VRAI_SHIM = _core.shim


@pytest.fixture
def contexte():
    ctx = VRAI_SHIM.asyncrdp_context_new(ffi.NULL)
    assert ctx != ffi.NULL
    yield ctx
    VRAI_SHIM.asyncrdp_context_free(ctx)


class _ShimEnEchec:
    """Vrai shim qui enregistre chaque appel ; les fonctions nommées renvoient 0 (échec FreeRDP)."""

    def __init__(self, *en_echec: str) -> None:
        self.en_echec = set(en_echec)
        self.appels: list[tuple[str, tuple]] = []

    def __getattr__(self, nom: str):
        reelle = getattr(VRAI_SHIM, nom)

        def appel(*args):
            self.appels.append((nom, args))
            return 0 if nom in self.en_echec else reelle(*args)

        return appel

    def noms(self) -> list[str]:
        return [nom for nom, _ in self.appels]

    def arguments(self, nom: str) -> list[tuple]:
        return [args for n, args in self.appels if n == nom]


def _toutes_les_options() -> RdpOptions:
    return RdpOptions(
        width=1024,
        height=768,
        redirect_printers=True,
        printers=[PrinterMapping("Bureau", driver="MS Publisher Imagesetter", is_default=True), PrinterMapping("Brut")],
        redirect_smartcards=True,
        drives=[DriveMapping("home", "/tmp")],
        serial_ports=[SerialMapping("COM1", "/dev/ttyS0")],
        parallel_ports=[ParallelMapping("LPT1", "/dev/lp0")],
        usb_devices=["id,dev:0483:5741", ""],
        audio_playback=True,
        audio_capture=True,
        monitors=[MonitorDef(0, 0, 1920, 1080, is_primary=True), MonitorDef(1920, 0, 1280, 1024)],
        gateway=GatewayOptions("gw.exemple.invalid", 8443, username="u", password="p", domain="CORP"),
        remoteapp=RemoteAppOptions("||notepad", name="Bloc-notes", cmdline="a.txt"),
        keyboard_layout=0x040C,
        load_balance_info=b"tsv://MS Terminal Services Plugin.1.Ferme",
        enable_graphics_pipeline=True,
        enable_h264=True,
    )


class TestApplySurUnVraiContexte:
    def test_options_par_defaut(self, contexte) -> None:
        assert RdpOptions()._apply(contexte) is True

    def test_toutes_les_options(self, contexte) -> None:
        assert _toutes_les_options()._apply(contexte) is True

    def test_passerelle_et_remoteapp_sans_champs_facultatifs(self, contexte) -> None:
        options = RdpOptions(
            gateway=GatewayOptions("gw.exemple.invalid"),
            remoteapp=RemoteAppOptions("||calc"),
            send_client_timezone=False,
        )
        assert options._apply(contexte) is True

    def test_bounding_box_multi_ecran_envoyee(self, contexte, monkeypatch) -> None:
        """DesktopWidth/Height = boîte englobante des moniteurs (pas le premier écran)."""
        shim = _ShimEnEchec()
        monkeypatch.setattr(_core, "shim", shim)
        options = RdpOptions(monitors=[MonitorDef(0, 0, 1920, 1080), MonitorDef(1920, 200, 1280, 1024)])
        assert options._apply(contexte) is True
        assert [args[1:] for args in shim.arguments("asyncrdp_set_resolution")] == [(3200, 1224)]
        assert shim.arguments("asyncrdp_set_use_multimon") == [(contexte, 1)]


class TestApplyEchecs:
    @pytest.mark.parametrize(
        "fonction",
        [
            "asyncrdp_add_printer",
            "asyncrdp_add_drive",
            "asyncrdp_add_serial_port",
            "asyncrdp_add_parallel_port",
            "asyncrdp_add_usb_device",
            "asyncrdp_set_color_depth",
            "asyncrdp_set_monitors",
            "asyncrdp_set_gateway",
            "asyncrdp_set_remoteapp",
            "asyncrdp_set_keyboard_layout",
            "asyncrdp_set_load_balance_info",
            "asyncrdp_set_graphics_pipeline",
        ],
    )
    def test_un_reglage_refuse_fait_echouer_apply(self, contexte, monkeypatch, fonction: str) -> None:
        shim = _ShimEnEchec(fonction)
        monkeypatch.setattr(_core, "shim", shim)
        assert _toutes_les_options()._apply(contexte) is False
        assert fonction in shim.noms()

    def test_fuseau_horaire_refuse_seulement_journalise(self, contexte, monkeypatch) -> None:
        monkeypatch.setattr(_core, "shim", _ShimEnEchec("asyncrdp_set_client_timezone"))
        assert RdpOptions()._apply(contexte) is True


def _port_ferme() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]  # libéré à la sortie : plus rien n'écoute


class TestConnectSansServeur:
    async def test_port_ferme_erreur_freerdp_et_contexte_libere(self, monkeypatch) -> None:
        shim = _ShimEnEchec()
        monkeypatch.setattr(_core, "shim", shim)
        options = RdpOptions(ignore_certificate=True, send_client_timezone=False)
        with pytest.raises(FreeRDPError, match=r"^\[\d+\] "):
            async with asyncrdp.connect(
                "127.0.0.1", _port_ferme(), username="u", password="p", domain="D", options=options
            ):
                pytest.fail("connexion inattendue")
        assert shim.noms().count("asyncrdp_context_free") == 1
        assert {"asyncrdp_set_username", "asyncrdp_set_password", "asyncrdp_set_domain"} <= set(shim.noms())
        assert _core._clients_by_context == {}

    async def test_contexte_impossible(self, monkeypatch) -> None:
        class SansContexte:
            def __getattr__(self, nom: str):
                if nom == "asyncrdp_context_new":
                    return lambda entry: ffi.NULL
                return getattr(VRAI_SHIM, nom)

        monkeypatch.setattr(_core, "shim", SansContexte())
        with pytest.raises(FreeRDPError, match="échec de création du contexte FreeRDP"):
            async with asyncrdp.connect("127.0.0.1"):
                pytest.fail("connexion inattendue")

    @pytest.mark.parametrize("fonction", ["asyncrdp_set_hostname", "asyncrdp_set_resolution"])
    async def test_reglages_refuses_contexte_libere(self, monkeypatch, fonction: str) -> None:
        shim = _ShimEnEchec(fonction)
        monkeypatch.setattr(_core, "shim", shim)
        with pytest.raises(FreeRDPError, match="échec de configuration des rdpSettings"):
            async with asyncrdp.connect("127.0.0.1"):
                pytest.fail("connexion inattendue")
        assert shim.noms()[-1] == "asyncrdp_context_free"
        assert "asyncrdp_connect" not in shim.noms()


def test_raise_last_error_message_inconnu(contexte, monkeypatch) -> None:
    class SansMessage:
        def __getattr__(self, nom: str):
            if nom == "asyncrdp_get_last_error_string":
                return lambda code: ffi.NULL
            return getattr(VRAI_SHIM, nom)

    monkeypatch.setattr(_core, "shim", SansMessage())
    with pytest.raises(FreeRDPError, match=r"^\[0\] erreur inconnue$"):
        _core._raise_last_error(contexte)
