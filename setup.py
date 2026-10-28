# SPDX-License-Identifier: GPL-3.0-or-later
"""
setup.py minimal : le reste de la métadonnée du package vit dans
pyproject.toml (PEP 621) — ce fichier n'existe que parce que le mécanisme
cffi_modules (compilation du shim C à l'installation) n'est pas exprimable
en pyproject.toml pur, setuptools a encore besoin d'un appel setup()
explicite pour ça.
"""

from setuptools import setup

setup(
    cffi_modules=["build_ffi.py:ffibuilder"],
)
