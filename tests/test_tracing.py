# SPDX-License-Identifier: GPL-3.0-or-later
"""
tests/test_tracing.py — vérifie le décorateur `asyncrdp.tracing.traced`
en isolation, avec des fonctions/classes fabriquées pour l'occasion —
avant qu'il ne soit appliqué au vrai code de `_core.py`/`integrations/`
(même discipline que `test_no_circular_imports.py` : le détecteur est
prouvé sur des cas synthétiques avant d'être fait confiance sur le vrai
code).

Ne dépend ni de `gi`, ni d'un contexte FreeRDP réel — seulement de
`asyncrdp.tracing` (bibliothèque standard + loguru).
"""

import asyncio

import pytest
from loguru import logger

from asyncrdp.tracing import _format_call, _safe_repr, traced

# ---------------------------------------------------------------------
# Utilitaire de test : capture les messages TRACE sans toucher au sink
# par défaut du projet (ajoute puis retire un sink dédié).
# ---------------------------------------------------------------------


class _TraceCapture:
    def __init__(self):
        self.messages: list[str] = []
        self._sink_id: int | None = None

    def __enter__(self) -> "_TraceCapture":
        self._sink_id = logger.add(self.messages.append, level="TRACE", format="{message}")
        return self

    def __exit__(self, *exc_info) -> None:
        logger.remove(self._sink_id)


# ---------------------------------------------------------------------
# functools.wraps : identité préservée (dont dépend @ffi.def_extern(),
# voir docstring du module tracing.py — vérifié séparément en conditions
# réelles contre un vrai module cffi compilé, voir CLAUDE.md).
# ---------------------------------------------------------------------


def test_traced_preserves_name_and_docstring_of_a_sync_function():
    def original(x: int) -> int:
        """Docstring d'origine."""
        return x + 1

    wrapped = traced(original)
    assert wrapped.__name__ == "original"
    assert wrapped.__doc__ == "Docstring d'origine."
    assert wrapped.__wrapped__ is original


def test_traced_preserves_name_of_an_async_function():
    async def original_async(x: int) -> int:
        return x + 1

    wrapped = traced(original_async)
    assert wrapped.__name__ == "original_async"


# ---------------------------------------------------------------------
# Comportement fonctionnel : la valeur de retour et les exceptions
# traversent le décorateur sans changement.
# ---------------------------------------------------------------------


def test_traced_sync_function_returns_the_real_value():
    @traced
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_traced_sync_function_reraises_the_original_exception():
    @traced
    def boom() -> None:
        raise ValueError("échec attendu")

    with pytest.raises(ValueError, match="échec attendu"):
        boom()


def test_traced_async_function_returns_the_real_value():
    @traced
    async def add_async(a: int, b: int) -> int:
        await asyncio.sleep(0)
        return a + b

    assert asyncio.run(add_async(2, 3)) == 5


def test_traced_async_function_reraises_the_original_exception():
    @traced
    async def boom_async() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("échec async attendu")

    with pytest.raises(RuntimeError, match="échec async attendu"):
        asyncio.run(boom_async())


# ---------------------------------------------------------------------
# Paresse : sans sink TRACE actif (état par défaut du projet), le coût
# de mise en forme des arguments/résultat n'est PAS payé.
# ---------------------------------------------------------------------


def test_traced_does_not_format_arguments_when_no_trace_sink_is_active():
    calls = {"repr": 0}

    class CountingRepr:
        def __repr__(self) -> str:
            calls["repr"] += 1
            return "<CountingRepr>"

    @traced
    def uses_arg(value) -> str:
        return "ok"

    # Aucun sink TRACE ajouté ici : configuration par défaut du projet.
    result = uses_arg(CountingRepr())
    assert result == "ok"
    assert calls["repr"] == 0, (
        "le repr de l'argument a été calculé alors qu'aucun sink TRACE "
        "n'était actif — la paresse annoncée dans tracing.py ne tient pas"
    )


def test_traced_does_format_arguments_once_a_trace_sink_is_active():
    calls = {"repr": 0}

    class CountingRepr:
        def __repr__(self) -> str:
            calls["repr"] += 1
            return "<CountingRepr>"

    @traced
    def uses_arg(value) -> str:
        return "ok"

    with _TraceCapture() as capture:
        uses_arg(CountingRepr())

    assert calls["repr"] >= 1
    assert any("uses_arg" in msg for msg in capture.messages)
    assert any("<CountingRepr>" in msg for msg in capture.messages)


# ---------------------------------------------------------------------
# Contenu du message : nom qualifié, entrée puis sortie, durée.
# ---------------------------------------------------------------------


def test_traced_logs_entry_then_exit_with_qualified_name():
    @traced
    def greet(name: str) -> str:
        return f"salut {name}"

    with _TraceCapture() as capture:
        greet("mathilde")

    assert len(capture.messages) == 2
    assert capture.messages[0].startswith("→ ")
    assert "greet" in capture.messages[0]
    assert "mathilde" in capture.messages[0]
    assert capture.messages[1].startswith("← ")
    assert "salut mathilde" in capture.messages[1]
    assert "ms)" in capture.messages[1]


def test_traced_logs_exception_marker_on_failure():
    @traced
    def boom() -> None:
        raise ValueError("échec attendu")

    with _TraceCapture() as capture, pytest.raises(ValueError):
        boom()

    assert len(capture.messages) == 2
    assert capture.messages[1].startswith("✗ ")
    assert "ValueError" in capture.messages[1]
    assert "échec attendu" in capture.messages[1]


# ---------------------------------------------------------------------
# Rédaction des paramètres sensibles — connect()/RdpOptions/GatewayOptions
# manipulent de vrais mots de passe.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("param_name", ["password", "PASSWORD", "passwd", "pwd", "secret", "token", "auth_token"])
def test_traced_redacts_sensitive_parameter_names(param_name: str):
    ns: dict = {}
    exec(f"def f(username, {param_name}):\n    return 'ok'\n", ns)
    func = traced(ns["f"])

    with _TraceCapture() as capture:
        func("alice", "hunter2-secret-value")

    entry_message = capture.messages[0]
    assert "hunter2-secret-value" not in entry_message
    assert "<redacted>" in entry_message
    assert "alice" in entry_message  # le paramètre non sensible reste visible


def test_traced_redaction_also_applies_to_keyword_call():
    @traced
    def connect_like(host: str, password: str | None = None) -> bool:
        return True

    with _TraceCapture() as capture:
        connect_like("192.168.1.10", password="secretissime")

    assert "secretissime" not in capture.messages[0]
    assert "<redacted>" in capture.messages[0]


# ---------------------------------------------------------------------
# bytes/bytearray : jamais affichés en clair — seulement leur longueur.
# ---------------------------------------------------------------------


def test_safe_repr_never_dumps_raw_bytes():
    payload = b"\x00\x01BGRA-frame-payload" * 1000
    rendered = _safe_repr(payload)
    assert str(len(payload)) in rendered
    assert "BGRA-frame-payload" not in rendered


def test_traced_never_dumps_raw_bytes_in_entry_or_exit():
    @traced
    def push_frame(raw: bytes) -> bytes:
        return raw

    payload = b"secretpixels" * 500
    with _TraceCapture() as capture:
        push_frame(payload)

    for message in capture.messages:
        assert b"secretpixels".decode() not in message
    assert any(f"{len(payload)} o." in message for message in capture.messages)


# ---------------------------------------------------------------------
# Troncature des repr trop longs.
# ---------------------------------------------------------------------


def test_safe_repr_truncates_long_values():
    long_text = "x" * 500
    rendered = _safe_repr(long_text)
    assert len(rendered) < len(long_text)
    assert rendered.endswith("…")


# ---------------------------------------------------------------------
# self/cls réduit au nom de classe pour une méthode — jamais le repr
# complet de l'instance (peut être volumineux/contenir des données
# internes non destinées au log).
# ---------------------------------------------------------------------


def test_traced_method_shows_class_name_not_full_instance_repr():
    class Client:
        def __repr__(self) -> str:
            return "<Client MARQUEUR_NE_DOIT_PAS_APPARAITRE>"

        @traced
        def get_frame(self) -> str:
            return "frame-bytes"

    with _TraceCapture() as capture:
        Client().get_frame()

    assert "Client" in capture.messages[0]
    assert "MARQUEUR_NE_DOIT_PAS_APPARAITRE" not in capture.messages[0]


# ---------------------------------------------------------------------
# Repli quand la signature n'est pas introspectable (cas réel : les
# méthodes de types builtins C n'ont souvent pas de signature).
# ---------------------------------------------------------------------


def test_format_call_falls_back_when_signature_is_not_introspectable():
    # dict.update est un cas réel confirmé sans signature introspectable
    # sur cette version de Python (inspect.signature lève ValueError) —
    # sert ici uniquement à déclencher la branche de repli de
    # _format_call, pas à être appelé.
    rendered = _format_call(dict.update, ({"a": 1}, {"b": 2}), {})
    assert "{'a': 1}" in rendered
    assert "{'b': 2}" in rendered


def test_format_call_fallback_also_redacts_sensitive_kwargs():
    rendered = _format_call(dict.update, (), {"password": "hunter2"})
    assert "hunter2" not in rendered
    assert "<redacted>" in rendered


# ---------------------------------------------------------------------
# Cas limites de _safe_repr / _format_call (issue #50, palier 2).
# ---------------------------------------------------------------------


def test_safe_repr_survives_a_failing_repr():
    class _ReprCasse:
        def __repr__(self):
            raise RuntimeError("repr cassé")

    assert _safe_repr(_ReprCasse()) == "<repr() a levé RuntimeError('repr cassé')>"


def test_format_call_skips_parameters_left_to_their_default():
    def f(a, b=2):
        return a + b

    assert _format_call(f, (1,), {}) == "a=1"


def test_format_call_expands_var_positional_and_redacts_var_keyword():
    def f(*valeurs, **options):
        return valeurs, options

    rendered = _format_call(f, (1, b"xx"), {"mode": "rapide", "api_token": "s3cr3t"})
    assert rendered == "1, <bytes: 2 o.>, mode='rapide', api_token=<redacted>"
