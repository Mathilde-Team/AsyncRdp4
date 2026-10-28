# SPDX-License-Identifier: GPL-3.0-or-later
"""Vérifie la licence GPL-3.0-or-later du dépôt (issue #68)."""

import re
import subprocess
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"
EXTENSIONS = (".py", ".sh", ".c", ".h", ".rules")


def test_fichier_license_gplv3():
    texte = (RACINE / "LICENSE").read_text(encoding="utf-8")
    assert texte.lstrip().startswith("GNU GENERAL PUBLIC LICENSE")
    assert "Version 3, 29 June 2007" in texte


def test_pyproject_declare_gpl():
    # Lecture textuelle : tomllib n'existe qu'à partir de Python 3.11.
    texte = (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^license = "GPL-3\.0-or-later"$', texte, re.MULTILINE)
    assert "<votre-compte>" not in texte


def test_entetes_spdx():
    fichiers = subprocess.run(
        ["git", "ls-files"], cwd=RACINE, capture_output=True, text=True, check=True
    ).stdout.split()
    manquants = []
    for nom in fichiers:
        if nom.startswith("docs/") or not nom.endswith(EXTENSIONS):
            continue
        tete = (RACINE / nom).read_text(encoding="utf-8").splitlines()[:3]
        if not any(SPDX in ligne for ligne in tete):
            manquants.append(nom)
    assert manquants == []
