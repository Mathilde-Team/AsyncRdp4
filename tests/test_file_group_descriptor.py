# SPDX-License-Identifier: GPL-3.0-or-later
"""
Tests unitaires du format FileGroupDescriptorW (MS-RDPECLIP §2.2.5.2.3) —
la partie clipboard fichiers la plus délicate à avoir bien codée (calcul
d'offsets, encodage UTF-16LE, gestion des dossiers).

Ces tests exercent la logique interne (_pack_file_group_descriptor /
_parse_file_group_descriptor) directement — pas besoin de serveur RDP,
juste un round-trip pack -> parse et vérification des valeurs.
"""

from asyncrdp._core import _pack_file_group_descriptor, _parse_file_group_descriptor


def test_pack_parse_single_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_bytes(b"contenu de test")

    data, abs_paths = _pack_file_group_descriptor([str(f)])
    entries = _parse_file_group_descriptor(data)

    assert len(entries) == 1
    assert entries[0].name == "hello.txt"
    assert entries[0].size == len(b"contenu de test")
    assert entries[0].is_directory is False
    assert abs_paths == [str(f)]


def test_pack_parse_multiple_files(tmp_path):
    names = ["a.txt", "b.bin", "c.dat"]
    paths = []
    for i, name in enumerate(names):
        f = tmp_path / name
        f.write_bytes(b"x" * (i + 1) * 100)
        paths.append(str(f))

    data, abs_paths = _pack_file_group_descriptor(paths)
    entries = _parse_file_group_descriptor(data)

    assert len(entries) == 3
    assert [e.name for e in entries] == names
    assert [e.size for e in entries] == [100, 200, 300]
    assert abs_paths == paths


def test_pack_parse_empty_list():
    data, abs_paths = _pack_file_group_descriptor([])
    entries = _parse_file_group_descriptor(data)
    assert entries == []
    assert abs_paths == []


def test_pack_parse_directory_recursive(tmp_path):
    """Un dossier sélectionné doit être parcouru récursivement, avec des
    noms relatifs utilisant '\\\\' comme séparateur (convention Windows,
    cf. MS-RDPECLIP) — pas os.sep."""
    root = tmp_path / "MyFolder"
    root.mkdir()
    (root / "top.txt").write_bytes(b"top level")
    sub = root / "Sub"
    sub.mkdir()
    (sub / "nested.txt").write_bytes(b"nested content")

    data, abs_paths = _pack_file_group_descriptor([str(root)])
    entries = _parse_file_group_descriptor(data)

    names = {e.name for e in entries}
    # L'entrée racine (le dossier lui-même), un fichier top-level, un
    # sous-dossier, et un fichier imbriqué avec '\' dans le nom relatif.
    assert "MyFolder" in names
    assert "MyFolder\\top.txt" in names
    assert "MyFolder\\Sub" in names
    assert "MyFolder\\Sub\\nested.txt" in names

    # Les entrées "dossier" n'ont pas de chemin absolu associé (rien à
    # transférer via FileContentsRequest pour elles).
    dir_entries = [e for e in entries if e.is_directory]
    assert len(dir_entries) == 2  # MyFolder + Sub
    for e in dir_entries:
        assert abs_paths[e.index] is None

    nested = next(e for e in entries if e.name == "MyFolder\\Sub\\nested.txt")
    assert nested.size == len(b"nested content")
    assert abs_paths[nested.index] == str(sub / "nested.txt")


def test_pack_parse_mixed_files_and_directories(tmp_path):
    f1 = tmp_path / "standalone.txt"
    f1.write_bytes(b"alone")
    d = tmp_path / "AFolder"
    d.mkdir()
    (d / "inside.txt").write_bytes(b"inside")

    data, _abs_paths = _pack_file_group_descriptor([str(f1), str(d)])
    entries = _parse_file_group_descriptor(data)

    names = {e.name for e in entries}
    assert names == {"standalone.txt", "AFolder", "AFolder\\inside.txt"}


def test_parse_truncated_data_does_not_crash():
    """Des octets tronqués (ex: coupure réseau en plein transfert) ne
    doivent jamais lever d'exception — juste renvoyer ce qui a pu être
    décodé."""
    # Compteur annonçant 5 entrées mais aucune donnée derrière.
    truncated = (5).to_bytes(4, "little")
    entries = _parse_file_group_descriptor(truncated)
    assert entries == []


def test_parse_empty_bytes():
    assert _parse_file_group_descriptor(b"") == []
    assert _parse_file_group_descriptor(b"\x00\x00") == []


def test_filename_with_non_ascii_characters(tmp_path):
    """Les noms de fichiers avec accents doivent survivre l'aller-retour
    UTF-16LE (cas d'usage réel : fichiers renommés en français)."""
    f = tmp_path / "résumé été.txt"
    f.write_bytes(b"data")

    data, _ = _pack_file_group_descriptor([str(f)])
    entries = _parse_file_group_descriptor(data)

    assert entries[0].name == "résumé été.txt"
