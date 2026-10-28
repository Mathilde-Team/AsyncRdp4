# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_no_circular_imports.py — absence de cycle d'imports entre les
modules internes du projet (`src/asyncrdp/`, `integrations/gtk4/`).

Contexte : identifié comme écart réel (pas un choix délibéré) lors
d'une comparaison avec le catalogue de règles d'un autre projet
(voir CLAUDE.md, "Comparaison avec `PATTERNS.md` d'un autre projet",
2026-09-05, section 11 de ce catalogue). Même principe que ce
catalogue documentait : construire le graphe réel par analyse `ast`
(pas une relecture manuelle, qui rate facilement un import ajouté plus
tard), le valider acyclique par un parcours en profondeur classique
(couleurs blanc/gris/noir), et vérifier le détecteur lui-même
positivement (sur un graphe avec un cycle volontaire, fabriqué à la
main — pas en écrivant de vrais fichiers sur disque) avant de
l'appliquer au projet.

Ce fichier n'utilise que des `assert` nus (pas de fixtures pytest)
afin de rester exécutable directement via `python3`, sans dépendance à
pytest — même convention que `test_gtk4_clipboard_file_download.py`
(voir `_run_all()` en bas de fichier). Ne dépend ni de `gi`, ni du
binding cffi compilé : uniquement de la bibliothèque standard (`ast`,
`pathlib`) et des fichiers source `.py` du dépôt.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Racines dont les modules internes doivent former un graphe acyclique.
# `tests/` est volontairement exclu : les tests important le code
# testé est attendu (ce n'est pas un cycle applicatif), pas l'inverse.
_SCAN_ROOTS = (
    _REPO_ROOT / "src" / "asyncrdp",
    _REPO_ROOT / "integrations" / "gtk4",
)


def _module_name_for(path: Path) -> str:
    """Nom de module Python pointé par un fichier .py de `_SCAN_ROOTS`.

    `src/asyncrdp/__init__.py` -> "asyncrdp"
    `src/asyncrdp/_core.py`    -> "asyncrdp._core"
    `integrations/gtk4/gcm_gtk4_display_bridge.py` -> "gcm_gtk4_display_bridge"
    (les fichiers d'intégration ne font pas partie d'un paquet Python
    installé — ils sont importés par leur nom de fichier tel quel,
    cf. `tests/test_gtk4_bridges.py`.)
    """
    if path.stem == "__init__":
        return path.parent.name
    if path.parent.name == "asyncrdp":
        return f"asyncrdp.{path.stem}"
    return path.stem


def _discover_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root in _SCAN_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.py")):
            modules[_module_name_for(path)] = path
    return modules


def _resolve_import(node: ast.AST, current_module: str, known: set[str]) -> set[str]:
    """Noms de modules internes (parmi `known`) réellement importés par
    ce noeud `Import`/`ImportFrom`, résolution des imports relatifs
    (`level`) comprise."""
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in known:
                targets.add(alias.name)
    elif isinstance(node, ast.ImportFrom):
        if node.level and node.level > 0:
            # Import relatif (`from ._core import X` depuis asyncrdp/__init__.py) :
            # niveau 1 = paquet courant.
            # Pour un module "asyncrdp" (le paquet lui-même, __init__.py),
            # niveau 1 pointe vers "asyncrdp" ; pour "asyncrdp._core",
            # niveau 1 pointe aussi vers le paquet "asyncrdp".
            base = "asyncrdp" if current_module.startswith("asyncrdp") else current_module
            full = f"{base}.{node.module}" if node.module else base
            if full in known:
                targets.add(full)
            # `from asyncrdp._core import X` écrit en absolu ailleurs :
            if node.module in known:
                targets.add(node.module)
        else:
            module = node.module or ""
            if module in known:
                targets.add(module)
            # `from asyncrdp import X` (les ponts GTK4) : le module
            # importé est le paquet lui-même.
            for name in known:
                if module == name:
                    targets.add(name)
    return targets


def build_import_graph(modules: dict[str, Path]) -> dict[str, set[str]]:
    """Graphe {module: {modules internes importés}} construit par
    analyse ast des fichiers de `modules`."""
    known = set(modules)
    graph: dict[str, set[str]] = {name: set() for name in known}
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                graph[name] |= _resolve_import(node, name, known)
        graph[name].discard(name)  # un module ne "dépend" pas de lui-même
    return graph


def find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """Parcours en profondeur classique à trois couleurs
    (blanc = jamais visité, gris = en cours de visite sur la pile
    d'appel courante, noir = fini) : un arc vers un noeud gris signale
    un cycle. Renvoie le cycle trouvé (liste de noms, dans l'ordre,
    premier noeud répété en fin de liste) ou None si acyclique.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(graph, WHITE)
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for neighbour in sorted(graph.get(node, ())):
            if color.get(neighbour, WHITE) == GRAY:
                cycle_start = stack.index(neighbour)
                return [*stack[cycle_start:], neighbour]
            if color.get(neighbour, WHITE) == WHITE:
                found = visit(neighbour)
                if found is not None:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in sorted(graph):
        if color[node] == WHITE:
            result = visit(node)
            if result is not None:
                return result
    return None


# --- Le détecteur est vérifié positivement avant d'être appliqué ------


def test_find_cycle_detects_a_direct_two_node_cycle():
    graph = {"a": {"b"}, "b": {"a"}}
    cycle = find_cycle(graph)
    assert cycle is not None
    assert set(cycle) == {"a", "b"}


def test_find_cycle_detects_an_indirect_three_node_cycle():
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}}
    cycle = find_cycle(graph)
    assert cycle is not None
    assert set(cycle) == {"a", "b", "c"}


def test_find_cycle_returns_none_on_an_acyclic_diamond():
    graph = {"a": {"b", "c"}, "b": {"d"}, "c": {"d"}, "d": set()}
    assert find_cycle(graph) is None


def test_find_cycle_ignores_shared_dependency_visited_from_two_branches():
    # Régression possible d'une implémentation naïve : "d" est atteint
    # deux fois (depuis "b" et depuis "c") sans qu'il y ait de cycle -
    # une confusion visité/en cours de visite ferait remonter un faux
    # positif ici.
    graph = {"a": {"b"}, "b": {"c", "d"}, "c": {"d"}, "d": set()}
    assert find_cycle(graph) is None


# --- Extraction du vrai graphe et vérification de complétude ---------


def test_discover_modules_finds_the_expected_project_modules():
    modules = _discover_modules()
    # Les deux fichiers du paquet asyncrdp et les quatre modules
    # d'intégration GTK4 (les deux ponts + gcm_gtk4_rdp_session/
    # gcm_gtk4_demo_viewer, ajoutés le 2026-09-18 pour assembler les
    # ponts dans une vraie application hôte, cf. CLAUDE.md) sont
    # attendus ; ce test échoue franchement si un fichier a été
    # déplacé/renommé sans que ce test soit mis à jour, plutôt que de
    # laisser le test anti-cycle "réussir" silencieusement sur un
    # graphe incomplet.
    expected = {
        "asyncrdp",
        "asyncrdp._core",
        "gcm_gtk4_display_bridge",
        "gcm_gtk4_clipboard_bridge",
        "gcm_gtk4_rdp_session",
        "gcm_gtk4_demo_viewer",
    }
    assert expected <= set(modules)


def test_import_graph_matches_known_dependencies():
    modules = _discover_modules()
    graph = build_import_graph(modules)
    # Dépendances internes réellement observées dans le code au moment
    # de l'écriture de ce test (cf. grep manuel de contrôle) : les
    # ponts GTK4 dépendent du paquet asyncrdp, le paquet expose _core,
    # _core dépend de tracing (décorateur @traced, ajouté le 2026-09-06 —
    # voir CLAUDE.md « Décorateur de traçabilité entrée/sortie ajouté »),
    # les deux ponts GTK4 en dépendent aussi directement (ils appliquent
    # @traced à leurs propres fonctions). tracing lui-même ne dépend
    # d'aucun autre module interne (voir sa docstring : doit rester une
    # feuille du graphe, sans quoi @traced appliqué à _core.py créerait
    # un cycle).
    assert graph["asyncrdp"] == {"asyncrdp._core"}
    assert graph["asyncrdp._core"] == {"asyncrdp.tracing"}
    assert graph["asyncrdp.tracing"] == set()
    assert graph["gcm_gtk4_display_bridge"] == {"asyncrdp", "asyncrdp.tracing"}
    assert graph["gcm_gtk4_clipboard_bridge"] == {"asyncrdp", "asyncrdp.tracing"}
    # gcm_gtk4_rdp_session (2026-09-18) assemble RdpView + ClipboardBridge :
    # dépend donc réellement de gcm_gtk4_clipboard_bridge (import du niveau
    # module, construit un ClipboardBridge) et, seulement pour des
    # annotations de type sous `if TYPE_CHECKING:` (jamais exécutées),
    # de gcm_gtk4_display_bridge et asyncrdp (ast.walk() parcourt aussi
    # ces imports différés — voir build_import_graph ci-dessus — d'où
    # leur présence ici malgré l'absence de dépendance réelle à
    # l'exécution).
    assert graph["gcm_gtk4_rdp_session"] == {
        "gcm_gtk4_clipboard_bridge",
        "gcm_gtk4_display_bridge",
        "asyncrdp",
        "asyncrdp.tracing",
    }
    # gcm_gtk4_demo_viewer (2026-09-18) est l'application hôte de
    # référence : dépend réellement de gcm_gtk4_display_bridge (RdpView),
    # gcm_gtk4_rdp_session (RdpSession) et asyncrdp (RdpOptions/
    # DriveMapping/FreeRDPError/connect).
    # + gcm_gtk4_asyncio (2026-10-11, issue #41) : intégration asyncio/GLib
    # (gi.events, gbulb en repli), feuille du graphe.
    assert graph["gcm_gtk4_demo_viewer"] == {
        "gcm_gtk4_display_bridge",
        "gcm_gtk4_rdp_session",
        "gcm_gtk4_asyncio",
        "asyncrdp",
        "asyncrdp.tracing",
    }
    assert graph["gcm_gtk4_asyncio"] == set()


# --- La vraie vérification --------------------------------------------


def test_project_internal_imports_are_acyclic():
    modules = _discover_modules()
    graph = build_import_graph(modules)
    cycle = find_cycle(graph)
    assert cycle is None, f"cycle d'imports détecté : {' -> '.join(cycle)}"


def _run_all() -> None:
    """Petit exécuteur maison : permet de lancer ce fichier avec
    `python3 tests/test_no_circular_imports.py` dans un environnement
    sans pytest installé (ex. sandbox sans accès réseau)."""
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failures.append((name, exc))
            print(f"FAIL {name}: {exc!r}")
        else:
            print(f"PASS {name}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} tests passés")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
