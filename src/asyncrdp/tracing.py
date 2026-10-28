# SPDX-License-Identifier: GPL-3.0-or-later
"""
tracing.py — décorateur de traçabilité entrée/sortie, systématique.

Contexte : écart identifié (pas un choix délibéré) lors de la comparaison
avec le catalogue de règles d'un autre projet (voir CLAUDE.md, section
« Comparaison avec PATTERNS.md d'un autre projet », 2026-09-05). Jusqu'ici
`_core.py` posait des `logger.debug()` ponctuels à la main à certains
points choisis (négociation multi-écran, RD Gateway, RemoteApp...), pas
une couverture garantie structurellement sur chaque fonction. Ce module
fournit un décorateur unique (`traced`), appliqué systématiquement aux
fonctions et méthodes de `src/asyncrdp/` et `integrations/gtk4/`, pour
qu'un point de log d'entrée/sortie identique existe partout sans dépendre
de ce qu'un développeur pense à ajouter à la main à un endroit précis.

Décisions de conception, volontaires :

- Niveau `TRACE` (le plus fin de loguru, sous `DEBUG`) : n'apparaît dans
  aucune sortie tant qu'un sink explicite ne l'active pas — le
  comportement par défaut du projet (rien en dessous de `DEBUG`) n'est
  donc pas changé par ce décorateur.
- Évaluation paresseuse (`logger.opt(lazy=True)`, vérifié empiriquement :
  le callable n'est invoqué que si un sink accepterait effectivement le
  message — voir CLAUDE.md pour le détail de cette vérification). Important
  ici parce que certaines fonctions décorées (callbacks FFI de rendu,
  lecture de socket via `_on_fd_readable`) sont appelées à haute
  fréquence : sans ça, activer `traced` partout aurait un coût mesurable
  même trace désactivée.
- Rédaction des identifiants sensibles : tout paramètre dont le nom
  contient "password"/"passwd"/"pwd"/"secret"/"token" (insensible à la
  casse) est remplacé par `"<redacted>"` — `connect()`/`RdpOptions`/
  `GatewayOptions` manipulent de vrais mots de passe, un décorateur
  systématique ne doit pas devenir un moyen accidentel de les faire
  fuiter dans les logs.
- `bytes`/`bytearray`/`memoryview` jamais affichés en clair (seulement
  leur longueur) — évite de vider des trames vidéo ou des contenus de
  fichiers entiers dans un log de trace.
- `self`/`cls` jamais affiché en entier pour une méthode : seul le nom de
  la classe apparaît (`Client(...)` plutôt que le repr complet de
  l'instance, qui peut être volumineux ou déclencher des accès non
  souhaités en plein log).
- Repr de chaque valeur tronqué à `_MAX_REPR` caractères.
- Supporte aussi bien une fonction/méthode `def` classique qu'`async def`
  (détection via `inspect.iscoroutinefunction`).

Exclusion volontaire, documentée : `connect()` (`_core.py`), décoré par
`@asynccontextmanager`, n'est PAS décoré par `traced` cette session.
`@asynccontextmanager` transforme la fonction génératrice asynchrone en
une fabrique de gestionnaire de contexte — le corps réel (négociation,
`yield`, nettoyage dans le `finally`) ne s'exécute que plus tard, piloté
par `async with`, pas au moment de l'appel. Un `traced` conçu pour une
coroutine ou une fonction synchrone classique ne mesurerait donc, à cet
endroit précis, que le temps de construction de l'objet gestionnaire de
contexte (quasi nul), pas le déroulement réel — pire, ce serait trompeur
plutôt qu'utile. Tracer correctement un générateur asynchrone (entrée,
chaque `yield`, sortie propre ou via exception côté `__aexit__`) est un
cas distinct, qui mérite sa propre conception/tests plutôt qu'une
extension hâtive ici, sur la fonction la plus sensible du projet (ouvre
de vraies connexions réseau et alloue un vrai contexte FreeRDP à libérer
sur tous les chemins de sortie). `connect()` garde donc uniquement ses
`logger.info()`/`logger.debug()`/`logger.error()` déjà en place (couverture
manuelle mais déjà assez complète : début, succès, chaque échec).

Autre exclusion volontaire : les fonctions imbriquées (closures définies
à l'intérieur d'une autre fonction, ex. `fetch_one` dans
`ClipboardBridge._download_and_set_files`) ne sont pas décorées
individuellement — la fonction englobante l'est déjà, et chaque
invocation de la closure porterait de toute façon le même nom qualifié
(`<locals>.fetch_one`), sans valeur ajoutée pour distinguer les appels
entre eux dans un log de trace.

Ce module ne dépend d'aucun autre module interne du projet — seulement de
la bibliothèque standard et de `loguru` (déjà une dépendance du projet) —
pour rester une feuille du graphe d'imports (voir
`tests/test_no_circular_imports.py`, qui fige cette contrainte).
"""

from __future__ import annotations

import functools
import inspect
import time
from collections.abc import Callable
from typing import Any, TypeVar

from loguru import logger

__all__ = ["traced"]

F = TypeVar("F", bound=Callable[..., Any])

_REDACT_MARKERS = ("password", "passwd", "pwd", "secret", "token")
_MAX_REPR = 120


def _is_sensitive(param_name: str) -> bool:
    lowered = param_name.lower()
    return any(marker in lowered for marker in _REDACT_MARKERS)


def _safe_repr(value: Any) -> str:
    """Repr tronqué et sans danger pour le journal : jamais de contenu
    binaire brut, jamais plus de `_MAX_REPR` caractères."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{type(value).__name__}: {len(value)} o.>"
    try:
        text = repr(value)
    except Exception as exc:
        return f"<repr() a levé {exc!r}>"
    if len(text) > _MAX_REPR:
        return text[:_MAX_REPR] + "…"
    return text


def _format_call(func: Callable[..., Any], args: tuple, kwargs: dict) -> str:
    """Représentation entrée sûre d'un appel : `self`/`cls` réduit au nom
    de classe, paramètres sensibles rédigés, reste rendu via
    `_safe_repr`. Retombe sur une liste positionnelle brute (toujours
    sûre) si la signature n'est pas introspectable."""
    try:
        signature = inspect.signature(func)
        bound = signature.bind_partial(*args, **kwargs)
    except (TypeError, ValueError):
        bound = None

    if bound is None:
        parts = [_safe_repr(a) for a in args]
        parts.extend(f"{k}={'<redacted>' if _is_sensitive(k) else _safe_repr(v)}" for k, v in kwargs.items())
        return ", ".join(parts)

    parts: list[str] = []
    param_names = list(signature.parameters)
    if param_names and param_names[0] in ("self", "cls") and args:
        parts.append(type(args[0]).__name__)
        param_names = param_names[1:]

    for name in param_names:
        if name not in bound.arguments:
            continue
        param = signature.parameters[name]
        value = bound.arguments[name]
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            parts.extend(_safe_repr(v) for v in value)
            continue
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            parts.extend(f"{k}={'<redacted>' if _is_sensitive(k) else _safe_repr(v)}" for k, v in value.items())
            continue
        rendered = "<redacted>" if _is_sensitive(name) else _safe_repr(value)
        parts.append(f"{name}={rendered}")
    return ", ".join(parts)


def traced(func: F) -> F:
    """Décore `func` (fonction, méthode, `def` ou `async def`, y compris
    les callbacks `@ffi.def_extern()` — le nom est préservé via
    `functools.wraps`, ce dont dépend l'enregistrement cffi) pour
    journaliser son entrée, sa sortie (valeur ou exception) et sa durée
    au niveau `TRACE` de loguru. Voir la docstring du module pour les
    choix de conception (rédaction, paresse, troncature, exclusions)."""
    qualname = getattr(func, "__qualname__", getattr(func, "__name__", repr(func)))

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.opt(lazy=True).trace("→ {} ({})", lambda: qualname, lambda: _format_call(func, args, kwargs))
            start = time.perf_counter()
            try:
                result = await func(*args, **kwargs)
            except BaseException as exc:
                # `exc` est capturé tout de suite (pas dans une lambda) : la
                # variable liée par `except ... as exc` est supprimée par
                # Python à la sortie du bloc, avant que loguru n'ait
                # nécessairement évalué les lambdas paresseuses.
                exc_repr = repr(exc)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.opt(lazy=True).trace(
                    "✗ {} a levé {} après {:.3f} ms",
                    lambda: qualname,
                    lambda: exc_repr,
                    lambda: elapsed_ms,
                )
                raise
            else:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.opt(lazy=True).trace(
                    "← {} -> {} ({:.3f} ms)",
                    lambda: qualname,
                    lambda: _safe_repr(result),
                    lambda: elapsed_ms,
                )
                return result

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.opt(lazy=True).trace("→ {} ({})", lambda: qualname, lambda: _format_call(func, args, kwargs))
        start = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            # Voir le commentaire équivalent dans `async_wrapper` ci-dessus.
            exc_repr = repr(exc)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.opt(lazy=True).trace(
                "✗ {} a levé {} après {:.3f} ms",
                lambda: qualname,
                lambda: exc_repr,
                lambda: elapsed_ms,
            )
            raise
        else:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.opt(lazy=True).trace(
                "← {} -> {} ({:.3f} ms)",
                lambda: qualname,
                lambda: _safe_repr(result),
                lambda: elapsed_ms,
            )
            return result

    return sync_wrapper  # type: ignore[return-value]
