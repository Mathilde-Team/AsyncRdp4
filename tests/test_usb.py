# SPDX-License-Identifier: GPL-3.0-or-later
"""
test_usb.py — tests de `asyncrdp.usb`.

Deux catégories, comme le reste du projet distingue toujours logique pure
et conditions réelles (voir `tests/gtk_stub/` pour le même principe côté
GTK4) :

1. `usb_device_args()` / `usb_device_args_auto()` : pure logique de
   formatage, aucune dépendance à `pyusb` ou à du matériel — testée
   directement.

2. `list_usb_devices()` :
   - Un test avec un stub minimal de `usb.core`/`usb.util` (deux faux
     périphériques, dont un dont la lecture des descripteurs de chaîne
     lève délibérément — pour vérifier le comportement documenté :
     inclus quand même, champs `None`) — logique pure, ne nécessite ni
     `pyusb` ni matériel réel.
   - Un test qui appelle la VRAIE fonction contre le VRAI `pyusb`/
     `libusb` installés dans cet environnement (pas de stub) : vérifie
     que l'appel aboutit sans exception et renvoie une liste. Ce sandbox
     ne présente aucun périphérique USB réel (cohérent avec toutes les
     sessions précédentes sur ce projet, voir `features.md`) — la liste
     obtenue est donc vide, mais l'appel réel à `libusb` en lui-même est
     confirmé fonctionnel, pas seulement supposé. Ignoré proprement
     (`skip`) si `pyusb` n'est pas installé (extra `usb` non demandé).

3. `usb_device_node_path()` / `usb_has_rw_access()` (ajout 2026-09-10,
   diagnostic de permissions, voir `udev/70-asyncrdp-usb.rules`) :
   logique pure sur bus/adresse pour la première ; pour la seconde, le
   cas positif s'exécute contre un vrai fichier réel (seul le chemin du
   device node est redirigé), le cas « accès refusé » simule
   `os.access()` (ce sandbox tourne en root, qui outrepasse les bits de
   permission classiques — un vrai `chmod` ne peut donc pas reproduire
   ce cas ici), et le cas « node absent » n'a besoin d'aucun mock.
"""

from __future__ import annotations

import sys
import types

import pytest

from asyncrdp.usb import (
    UsbDeviceInfo,
    list_usb_devices,
    usb_device_args,
    usb_device_args_auto,
    usb_device_node_path,
    usb_has_rw_access,
)


def test_usb_device_args_from_device_info():
    dev = UsbDeviceInfo(vendor_id=0x0483, product_id=0x5741, bus=1, address=2)
    assert usb_device_args(dev) == "id,dev:0483:5741"


def test_usb_device_args_from_tuple():
    assert usb_device_args((0x054C, 0x0268)) == "id,dev:054c:0268"


def test_usb_device_args_zero_padded():
    # VID/PID sur moins de 4 chiffres hexa : doit rester zero-paddé à 4,
    # format /usb: de xfreerdp attend une largeur fixe.
    assert usb_device_args((0x1, 0xAB)) == "id,dev:0001:00ab"


def test_usb_device_args_auto():
    assert usb_device_args_auto() == "auto"


def test_usb_device_node_path_zero_padded():
    dev = UsbDeviceInfo(vendor_id=0x0483, product_id=0x5741, bus=1, address=7)
    assert usb_device_node_path(dev) == "/dev/bus/usb/001/007"


def test_usb_device_node_path_no_padding_needed():
    # bus/adresse déjà sur 3 chiffres : ne doit pas être tronqué ni
    # re-paddé au-delà de 3 (contrairement à usb_device_args(), qui
    # zero-padde le VID:PID à 4 chiffres hexa — largeurs différentes,
    # ne pas confondre les deux formats).
    dev = UsbDeviceInfo(vendor_id=0x0483, product_id=0x5741, bus=12, address=134)
    assert usb_device_node_path(dev) == "/dev/bus/usb/012/134"


def test_usb_has_rw_access_true_on_real_accessible_file(tmp_path, monkeypatch):
    # Cas positif testé contre un VRAI fichier réel (pas mocké) : seul le
    # chemin du device node est redirigé vers un fichier de test, la
    # vérification os.access() elle-même s'exécute pour de vrai.
    dev = UsbDeviceInfo(vendor_id=0x0483, product_id=0x5741, bus=1, address=7)
    fake_node = tmp_path / "fake-usb-node"
    fake_node.write_bytes(b"")
    monkeypatch.setattr("asyncrdp.usb.usb_device_node_path", lambda _dev: str(fake_node))
    assert usb_has_rw_access(dev) is True


def test_usb_has_rw_access_false_when_permission_denied(tmp_path, monkeypatch):
    # Le node existe réellement mais os.access() est simulé pour refuser
    # l'accès — reproduit le cas documenté (droits udev insuffisants sur
    # /dev/bus/usb/...) sans dépendre d'un vrai device node ni d'un
    # utilisateur non-root (ce sandbox tourne en root, qui outrepasse les
    # bits de permission classiques : chmod seul ne peut donc pas
    # reproduire ce cas ici).
    dev = UsbDeviceInfo(vendor_id=0x0483, product_id=0x5741, bus=1, address=7)
    fake_node = tmp_path / "fake-usb-node-locked"
    fake_node.write_bytes(b"")
    monkeypatch.setattr("asyncrdp.usb.usb_device_node_path", lambda _dev: str(fake_node))
    monkeypatch.setattr("os.access", lambda _path, _mode: False)
    assert usb_has_rw_access(dev) is False


def test_usb_has_rw_access_none_when_node_missing():
    # Périphérique débranché entre l'énumération et l'appel (ou bus/
    # adresse obsolètes) : le device node n'existe pas du tout, pas
    # seulement inaccessible — None, pas False, pour ne pas laisser
    # croire à un blocage de permissions réparable par udev.
    dev = UsbDeviceInfo(vendor_id=0x0483, product_id=0x5741, bus=999, address=999)
    assert usb_has_rw_access(dev) is None


def test_usb_device_info_str_prefers_product_name():
    dev = UsbDeviceInfo(
        vendor_id=0x0483,
        product_id=0x5741,
        bus=1,
        address=2,
        manufacturer="STMicroelectronics",
        product="Virtual COM Port",
    )
    assert "Virtual COM Port" in str(dev)
    assert "0483:5741" in str(dev)


class _FakeUsbError(Exception):
    pass


class _FakeDevice:
    def __init__(
        self,
        vendor_id,
        product_id,
        bus,
        address,
        manufacturer=None,
        product=None,
        serial_number=None,
        raise_on_string_read=False,
    ):
        self.idVendor = vendor_id
        self.idProduct = product_id
        self.bus = bus
        self.address = address
        # Descripteurs de chaîne non nuls => get_string() sera appelé.
        # Indices distincts (1/2/3), comme sur un vrai périphérique USB —
        # un stub qui les confondrait masquerait un vrai bug de mapping.
        self.iManufacturer = 1 if manufacturer is not None or raise_on_string_read else 0
        self.iProduct = 2 if product is not None or raise_on_string_read else 0
        self.iSerialNumber = 3 if serial_number is not None or raise_on_string_read else 0
        self._manufacturer = manufacturer
        self._product = product
        self._serial_number = serial_number
        self._raise_on_string_read = raise_on_string_read


def _install_fake_pyusb(monkeypatch, devices):
    """Installe un faux module `usb.core`/`usb.util` dans sys.modules,
    restauré automatiquement par monkeypatch en fin de test."""

    fake_core = types.ModuleType("usb.core")
    fake_util = types.ModuleType("usb.util")
    fake_usb = types.ModuleType("usb")
    fake_usb.core = fake_core
    fake_usb.util = fake_util

    fake_core.USBError = _FakeUsbError
    fake_core.find = lambda find_all=True: iter(devices)

    # Résolution volontairement simplifiée : chaque faux device porte
    # directement ses valeurs attendues (`_manufacturer`/`_product`/
    # `_serial_number`), on n'a pas besoin de répliquer toute la
    # sémantique des tables de descripteurs de chaîne d'un vrai
    # périphérique USB pour ce stub.
    def get_string(dev, index):
        if dev._raise_on_string_read:
            raise _FakeUsbError("lecture du descripteur de chaîne refusée (udev)")
        if index == dev.iManufacturer and dev._manufacturer is not None:
            return dev._manufacturer
        if index == dev.iProduct and dev._product is not None:
            return dev._product
        if index == dev.iSerialNumber and dev._serial_number is not None:
            return dev._serial_number
        return None

    fake_util.get_string = get_string

    monkeypatch.setitem(sys.modules, "usb", fake_usb)
    monkeypatch.setitem(sys.modules, "usb.core", fake_core)
    monkeypatch.setitem(sys.modules, "usb.util", fake_util)


def test_list_usb_devices_pure_logic_with_stub(monkeypatch):
    ok_device = _FakeDevice(
        vendor_id=0x0483,
        product_id=0x5741,
        bus=1,
        address=5,
        manufacturer="STMicroelectronics",
        product="Virtual COM Port",
        serial_number="ABC123",
    )
    device_without_permission = _FakeDevice(
        vendor_id=0x1234,
        product_id=0xABCD,
        bus=1,
        address=6,
        raise_on_string_read=True,
    )
    _install_fake_pyusb(monkeypatch, [ok_device, device_without_permission])

    devices = list_usb_devices()

    assert len(devices) == 2

    first = devices[0]
    assert (first.vendor_id, first.product_id) == (0x0483, 0x5741)
    assert first.bus == 1 and first.address == 5
    assert first.manufacturer == "STMicroelectronics"
    assert first.product == "Virtual COM Port"
    assert first.serial_number == "ABC123"
    assert usb_device_args(first) == "id,dev:0483:5741"

    # Périphérique dont la lecture des descripteurs de chaîne échoue :
    # doit quand même apparaître dans le résultat (comportement documenté
    # dans list_usb_devices()), avec ces trois champs à None plutôt que
    # de faire échouer toute l'énumération.
    second = devices[1]
    assert (second.vendor_id, second.product_id) == (0x1234, 0xABCD)
    assert second.manufacturer is None
    assert second.product is None
    assert second.serial_number is None
    assert usb_device_args(second) == "id,dev:1234:abcd"


def test_list_usb_devices_empty_with_stub(monkeypatch):
    _install_fake_pyusb(monkeypatch, [])
    assert list_usb_devices() == []


def test_list_usb_devices_raises_clear_error_without_pyusb(monkeypatch):
    # Simule pyusb non installé : `usb` absent de sys.modules ET
    # non trouvable par le mécanisme d'import normal. On ne peut pas
    # facilement "désinstaller" un vrai pyusb déjà présent dans ce
    # sandbox : on force plutôt l'échec de l'import via un faux
    # meta_path finder minimal, restauré par monkeypatch en fin de test.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "usb" or name.startswith("usb."):
            raise ImportError("pyusb non installé (simulé pour ce test)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "usb", raising=False)
    monkeypatch.delitem(sys.modules, "usb.core", raising=False)
    monkeypatch.delitem(sys.modules, "usb.util", raising=False)

    with pytest.raises(RuntimeError, match="pip install asyncrdp\\[usb\\]"):
        list_usb_devices()


def test_list_usb_devices_against_real_libusb():
    """Appel réel, sans stub — confirme que le chemin pyusb -> libusb
    fonctionne bout en bout dans cet environnement (voir docstring de
    `list_usb_devices` : 0 résultat attendu ici faute de matériel USB
    réel dans ce sandbox, mais l'appel lui-même ne doit pas lever)."""
    pytest.importorskip("usb.core", reason="extra 'usb' (pyusb) non installé")
    devices = list_usb_devices()
    assert isinstance(devices, list)
    for dev in devices:  # pragma: no cover — dépend du matériel hôte réel
        assert isinstance(dev, UsbDeviceInfo)
