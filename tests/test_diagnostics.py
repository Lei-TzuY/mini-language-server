from __future__ import annotations

import pytest

from mini_language_server import DocumentStore, Span
from mini_language_server.diagnostics import Diagnostic, DiagnosticError, DiagnosticStore
from mini_language_server.semantic import Reference, SemanticDatabase
from mini_language_server.symbols import Symbol, SymbolIndex
from mini_language_server.syntax import SyntaxStore


def current_semantics():
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    document = documents.open(
        uri="file:///workspace/main.nova",
        language_id="nova",
        version=1,
        text="let answer = answer\n",
    )
    parsed = syntax.publish(document, ("module",))
    index = SymbolIndex(syntax)
    declaration = Symbol("answer", "variable", Span(4, 10))
    symbols = index.publish(parsed, [declaration])
    database = SemanticDatabase(index)
    semantic = database.publish(symbols, [Reference(Span(13, 19), declaration)])
    return documents, syntax, index, database, parsed, symbols, declaration, semantic


def test_publish_is_deterministic_and_version_bound() -> None:
    *_, database, _, _, _, semantic = current_semantics()
    store = DiagnosticStore(database)

    snapshot = store.publish(
        semantic,
        [
            Diagnostic(Span(13, 19), "unused reference", "warning", "W001"),
            Diagnostic(Span(4, 10), "invalid declaration"),
        ],
    )

    assert store.get(semantic.uri) is snapshot
    assert snapshot.semantic is semantic
    assert snapshot.version == 1
    assert snapshot.diagnostics == (
        Diagnostic(Span(4, 10), "invalid declaration"),
        Diagnostic(Span(13, 19), "unused reference", "warning", "W001"),
    )


def test_semantic_republication_invalidates_diagnostics() -> None:
    *_, database, _, _, declaration, semantic = current_semantics()
    store = DiagnosticStore(database)
    old = store.publish(semantic, [Diagnostic(Span(4, 10), "old")])

    replacement = database.publish(
        semantic.symbols, [Reference(Span(13, 19), declaration)]
    )

    assert replacement is not semantic
    assert store.get(semantic.uri) is None
    with pytest.raises(DiagnosticError, match="stale diagnostic result"):
        store.publish(semantic, old.diagnostics)


def test_document_update_hides_diagnostics_and_rejects_late_result() -> None:
    documents, _, _, database, _, _, _, semantic = current_semantics()
    store = DiagnosticStore(database)
    old = store.publish(semantic, [Diagnostic(Span(4, 10), "old")])

    documents.replace(uri=semantic.uri, version=2, text="let answer = 42\n")

    assert database.get(semantic.uri) is None
    assert store.get(semantic.uri) is None
    with pytest.raises(DiagnosticError, match="stale diagnostic result"):
        store.publish(semantic, old.diagnostics)


def test_close_reopen_same_uri_and_version_does_not_revive_diagnostics() -> None:
    documents, syntax, index, database, _, _, _, semantic = current_semantics()
    store = DiagnosticStore(database)
    store.publish(semantic, [Diagnostic(Span(4, 10), "old")])

    documents.close(semantic.uri)
    reopened = documents.open(
        uri=semantic.uri,
        language_id="nova",
        version=1,
        text="let answer = 42\n",
    )
    parsed = syntax.publish(reopened, ("module",))
    declaration = Symbol("answer", "variable", Span(4, 10))
    symbols = index.publish(parsed, [declaration])
    current = database.publish(symbols, [])

    assert current.version == semantic.version
    assert current is not semantic
    assert store.get(semantic.uri) is None


def test_invalid_batch_does_not_replace_current_snapshot() -> None:
    *_, database, _, _, _, semantic = current_semantics()
    store = DiagnosticStore(database)
    current = store.publish(semantic, [Diagnostic(Span(4, 10), "current")])

    with pytest.raises(DiagnosticError, match="diagnostic span is outside"):
        store.publish(semantic, [Diagnostic(Span(4, 100), "invalid")])

    assert store.get(semantic.uri) is current


def test_overlapping_diagnostics_are_allowed_and_deterministic() -> None:
    *_, database, _, _, _, semantic = current_semantics()
    store = DiagnosticStore(database)

    snapshot = store.publish(
        semantic,
        [
            Diagnostic(Span(4, 10), "later", "warning"),
            Diagnostic(Span(4, 7), "earlier"),
            Diagnostic(Span(4, 10), "earlier message", "error"),
        ],
    )

    assert snapshot.diagnostics == (
        Diagnostic(Span(4, 7), "earlier"),
        Diagnostic(Span(4, 10), "earlier message", "error"),
        Diagnostic(Span(4, 10), "later", "warning"),
    )


def test_diagnostic_value_validation() -> None:
    with pytest.raises(DiagnosticError, match="non-empty string"):
        Diagnostic(Span(0, 0), "")
    with pytest.raises(DiagnosticError, match="unsupported diagnostic severity"):
        Diagnostic(Span(0, 0), "message", "fatal")
    with pytest.raises(DiagnosticError, match="code"):
        Diagnostic(Span(0, 0), "message", code="")
    with pytest.raises(DiagnosticError, match="source"):
        Diagnostic(Span(0, 0), "message", source="")
