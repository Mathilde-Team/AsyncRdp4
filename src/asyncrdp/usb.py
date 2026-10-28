# SPDX-License-Identifier: GPL-3.0-or-later
"""
usb.py — énumération des périphériques USB locaux (libusb, via pyusb) et
construction des chaînes de sélection attendues par `RdpOptions.usb_devices`
/ `asyncrdp_add_usb_device` (canal urbdrc).

Contexte : `features.md` marquait ce point 🔲 (« énumération des
périphériques (libusb) non faite, aucun matériel disponible pour tester »).
Le commentaire de `_shim.c` (`asyncrdp_add_usb_device`) est explicite :
transmettre une chaîne déjà construite au canal `urbdrc` est délibérément
tout ce que fait le shim — reconstruire soi-même l'énumération des bus USB
dans `_shim.c`/`_core.py` referait ce que `libusb` fait déjà. Mais rien
n'empêchait de fournir cette brique côté Python, en s'appuyant sur `libusb`
via `pyusb` plutôt que de laisser chaque appelant (GCM) réimplémenter
l'appel `libusb` et le format de chaîne `/usb:` de xfreerdp. C'est l'objet
de ce module.

Séparé de `_core.py` à dessein : `pyusb` n'est PAS une dépendance du cœur
de la bibliothèque (connexion RDP, frames, clipboard...), seulement de
cette fonctionnalité optionnelle — voir l'extra `usb` dans `pyproject.toml`.
L'import de `usb.core`/`usb.util` (module `pyusb`) est donc fait ici,
paresseusement (à l'intérieur des fonctions), pour que `import asyncrdp`
ne casse jamais pour qui n'a pas installé `pyusb`.

Statut réel de test, honnêtement : `list_usb_devices()` a été exécutée
dans ce sandbox avec `pyusb` + `libusb-1.0.so.0` réellement installés
(pas mocké) — l'appel à `usb.core.find(find_all=True)` aboutit sans lever
d'exception et renvoie une liste (vide : ce sandbox ne présente aucun
périphérique USB réel, cohérent avec toutes les sessions précédentes sur
ce projet). C'est donc confirmé : le mécanisme d'énumération lui-même
fonctionne bout en bout jusqu'à `libusb`. Ce qui reste non confirmé,
faute de matériel — comme déjà noté dans `features.md` avant ce module —
c'est qu'un périphérique USB réel apparaisse effectivement dans le
résultat, et qu'une chaîne construite par `usb_device_args()` redirige
effectivement ce périphérique une fois passée à `RdpOptions.usb_devices`
contre un vrai serveur RDP. Voir `CLAUDE.md` pour le détail.

Ajout du 2026-09-10 : le blocage de permissions constaté sur du vrai
matériel (`ValueError` « no langid » côté descripteurs de chaîne,
`LIBUSB_ERROR_ACCESS` côté redirection effective — les deux faute de
droits udev sur `/dev/bus/usb/...`) était jusqu'ici seulement documenté,
sans rien de livré pour le lever. Deux ajouts : `usb_has_rw_access()`,
qui permet à un appelant de diagnostiquer ce blocage par périphérique
*avant* de tenter une redirection plutôt que de laisser l'échec remonter
tard depuis libusb/urbdrc ; et `udev/70-asyncrdp-usb.rules` (règle
`TAG+="uaccess"`, voir ce fichier pour le détail et les compromis), que
`install.sh --usb` installe désormais automatiquement. Testé en logique
pure uniquement (permissions de fichiers simulées, voir `tests/test_usb.py`)
— aucun périphérique USB réel ni environnement `systemd-logind`/session
graphique active n'étant disponible dans ce sandbox de développement pour
confirmer que la règle udev elle-même produit bien l'ACL attendue une
fois installée sur une vraie machine. Reste donc ouvert, inchangé sur le
fond : une confirmation matérielle complète (règle installée + vrai
périphérique + vrai serveur RDP) — voir `docs/features-backlog.md`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from .tracing import traced


@dataclass(frozen=True)
class UsbDeviceInfo:
    """Un périphérique USB local tel que rapporté par libusb.

    `manufacturer`/`product`/`serial_number` peuvent être `None` : leur
    lecture nécessite une transaction USB de contrôle supplémentaire
    (descripteurs de chaîne) qui peut échouer sans droits suffisants sur
    le device node (`/dev/bus/usb/...`) même quand l'énumération de base
    (bus/adresse/VID/PID, qui ne nécessite qu'une lecture du descripteur
    de périphérique déjà en cache noyau) réussit — on ne fait donc pas
    échouer l'énumération entière pour ce seul détail, voir
    `list_usb_devices()`.
    """

    vendor_id: int
    product_id: int
    bus: int
    address: int
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None

    @traced
    def __str__(self) -> str:  # pragma: no cover — confort d'affichage
        label = self.product or self.manufacturer or "périphérique USB"
        return f"{label} ({self.vendor_id:04x}:{self.product_id:04x})"


@traced
def list_usb_devices() -> list[UsbDeviceInfo]:
    """Énumère les périphériques USB actuellement branchés, via libusb.

    Nécessite l'extra `pip install asyncrdp[usb]` (pyusb) — lève
    `RuntimeError` avec un message explicite si absent, plutôt qu'un
    `ImportError` brut, puisque ce module reste optionnel par design.

    Ne lève pas si la lecture des descripteurs de chaîne (fabricant/
    produit/numéro de série) échoue pour un périphérique donné (cas
    fréquent sans droits udev sur `/dev/bus/usb/...`) : ce périphérique
    est quand même inclus dans le résultat, avec ces trois champs à
    `None` — VID/PID/bus/adresse suffisent déjà pour construire une
    chaîne `usb_device_args()` utilisable.
    """
    try:
        import usb.core
        import usb.util
    except ImportError as exc:
        logger.error("pyusb absent, énumération USB indisponible : {}", exc)
        raise RuntimeError(
            "L'énumération USB nécessite pyusb : pip install asyncrdp[usb] (ou 'pip install pyusb' directement)."
        ) from exc

    devices: list[UsbDeviceInfo] = []
    for dev in usb.core.find(find_all=True):
        manufacturer = product = serial_number = None
        try:
            manufacturer = usb.util.get_string(dev, dev.iManufacturer) if dev.iManufacturer else None
            product = usb.util.get_string(dev, dev.iProduct) if dev.iProduct else None
            serial_number = usb.util.get_string(dev, dev.iSerialNumber) if dev.iSerialNumber else None
        except (usb.core.USBError, NotImplementedError, ValueError) as exc:
            # Cf. docstring : descripteurs de chaîne indisponibles (droits
            # udev, ou périphérique qui ne les expose simplement pas) —
            # pas fatal, l'appelant peut toujours construire une chaîne de
            # sélection sur le seul VID:PID. `ValueError` ("no langid") est
            # ce que pyusb lève réellement (pas `USBError`) sans droits
            # udev sur `/dev/bus/usb/...` — trouvé le 2026-09-07 sur du
            # vrai matériel (souris/clavier/webcam/Bluetooth d'un vrai
            # laptop), jamais déclenché avant faute de périphérique USB
            # réel à énumérer dans les sandbox précédents.
            logger.debug(
                "Descripteurs de chaîne indisponibles pour {:04x}:{:04x} ({})",
                dev.idVendor,
                dev.idProduct,
                exc,
            )
        devices.append(
            UsbDeviceInfo(
                vendor_id=dev.idVendor,
                product_id=dev.idProduct,
                bus=dev.bus,
                address=dev.address,
                manufacturer=manufacturer,
                product=product,
                serial_number=serial_number,
            )
        )
    return devices


@traced
def usb_device_node_path(device: UsbDeviceInfo) -> str:
    """Chemin Linux du device node correspondant à `device`
    (`/dev/bus/usb/BBB/DDD`, bus/adresse zero-paddés sur 3 chiffres comme
    le fait le noyau lui-même sous `usbfs`).

    Ne vérifie pas que ce chemin existe réellement ni qu'il est
    accessible — seulement pertinent sur Linux avec le layout `usbfs`
    standard (celui de tous les environnements de développement/test de
    ce projet à ce jour). Voir `usb_has_rw_access()` pour la vérification
    d'accès proprement dite.
    """
    return f"/dev/bus/usb/{device.bus:03d}/{device.address:03d}"


@traced
def usb_has_rw_access(device: UsbDeviceInfo) -> bool | None:
    """Diagnostic de permissions pour `device`, pensé pour être appelé
    par un appelant (ex. futur plugin GCM) *avant* de tenter une
    redirection, plutôt que de laisser l'échec remonter tard et de façon
    cryptique depuis libusb/urbdrc (`LIBUSB_ERROR_ACCESS`/
    `LIBUSB_ERROR_IO`, confirmé le 2026-09-07 contre un vrai second
    serveur RDP externe — voir `docs/features-backlog.md`, section USB).

    Renvoie :
    - `True`/`False` si le device node existe et que son accessibilité
      en lecture+écriture a pu être déterminée ;
    - `None` si le device node n'existe pas (périphérique débranché
      entre l'énumération et cet appel, par exemple) ou si ce chemin n'a
      pas de sens sur cette plateforme (non-Linux). Un `None` ne doit
      donc PAS être interprété comme « pas d'accès », seulement comme
      « impossible à déterminer ».

    N'ouvre pas le périphérique : une lecture de permissions fichier
    (`os.access`) suffit à reproduire le diagnostic qui a permis de
    trouver le blocage udev documenté dans `docs/features-backlog.md` —
    pas besoin d'une vraie tentative d'ouverture libusb pour ce seul
    diagnostic. Voir `udev/70-asyncrdp-usb.rules` pour la règle qui lève
    ce blocage.
    """
    path = usb_device_node_path(device)
    if not os.path.exists(path):
        return None
    return os.access(path, os.R_OK | os.W_OK)


@traced
def usb_device_args(device: UsbDeviceInfo | tuple[int, int]) -> str:
    """Construit la chaîne de sélection attendue par
    `RdpOptions.usb_devices` / `asyncrdp_add_usb_device` (canal `urbdrc`),
    au format `id,dev:VVVV:PPPP` documenté dans `_shim.c` (même syntaxe
    que l'option `/usb:` de `xfreerdp`, filtrage par VID:PID).

    Accepte soit un `UsbDeviceInfo` (typiquement issu de
    `list_usb_devices()`), soit directement un couple `(vendor_id,
    product_id)` pour qui a déjà ces identifiants sans passer par
    l'énumération (ex. valeurs connues à l'avance, hors de ce sandbox).

    Ne désambiguïse PAS par bus/adresse en cas de plusieurs périphériques
    identiques (même VID:PID) branchés simultanément — `_shim.c` ne
    documente que la forme VID:PID, et la forme `@bus-port` de xfreerdp
    n'a pas pu être vérifiée dans cet environnement (aucun second
    périphérique identique disponible pour confirmer le comportement
    réel côté `urbdrc`) ; à revoir si ce cas se présente.
    """
    if isinstance(device, UsbDeviceInfo):
        vendor_id, product_id = device.vendor_id, device.product_id
    else:
        vendor_id, product_id = device
    return f"id,dev:{vendor_id:04x}:{product_id:04x}"


@traced
def usb_device_args_auto() -> str:
    """Chaîne de sélection « tout rediriger », cf. `_shim.c` (`\"auto\"`
    accepté par `asyncrdp_add_usb_device` en plus du filtrage VID:PID).
    """
    return "auto"
