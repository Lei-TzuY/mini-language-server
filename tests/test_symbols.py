from __future__ import annotations

import pytest

from mini_language_server import DocumentStore, Span
from mini_language_server.symbols import Symbol, SymbolError, SymbolIndex
from mini_language_server.syntax import SyntaxStore


def current_syntax():
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    document = documents.open(
        uri="file:///workspace/main.nova",
        language_id="nova",
        version=1,
        text="let answer = answer\n",
    )
    snapshot = syntax.publish(document, ("module",))
    return documents, syntax, snapshot


def test_publish_orders_and_indexes_symbols_deterministically() -> None:
    _, syntax, parsed = current_syntax()
    index = SymbolIndex(syntax)
    late = Symbol("answer", "reference", Span(13, 19))
    declaration = Symbol("answer", "variable", Span(4, 10))

    snapshot = index.publish(parsed, [late, declaration])

    assert index.get(parsed.uri) is snapshot
    assert snapshot.syntax is parsed
    assert snapshot.version == 1
    assert snapshot.symbols == (declaration, late)
    assert snapshot.named("answer") == (declaration, late)
    assert snapshot.named("missing") == ()


def test_document_update_hides_symbol_snapshot() -> None:
    documents, syntax, parsed = current_syntax()
    index = SymbolIndex(syntax)
    index.publish(parsed, [Symbol("answer", "variable", Span(4, 10))])

    documents.replace(uri=parsed.uri, version=2, text="let answer = 43\n")

    assert syntax.get(parsed.uri) is None
    assert index.get(parsed.uri) is None


def test_late_symbol_publish_is_rejected_after_document_update() -> None:
    documents, syntax, parsed = current_syntax()
    index = SymbolIndex(syntax)
    documents.replace(uri=parsed.uri, version=2, text="let answer = 43\n")

    with pytest.raises(SymbolError, match="stale symbol result"):
        index.publish(parsed, [Symbol("answer", "variable", Span(4, 10))])

    assert index.get(parsed.uri) is None


def test_republished_syntax_invalidates_symbols_for_same_document() -> None:
    _, syntax, first = current_syntax()
    index = SymbolIndex(syntax)
    old = index.publish(first, [Symbol("answer", "variable", Span(4, 10))])

    second = syntax.publish(first.document, ("module", "reparsed"))

    assert second.document is first.document
    assert second is not first
    assert index.get(first.uri) is None
    with pytest.raises(SymbolError, match="stale symbol result"):
        index.publish(first, old.symbols)


def test_reopen_same_uri_and_version_does_not_resurrect_symbols() -> None:
    documents, syntax, parsed = current_syntax()
    index = SymbolIndex(syntax)
    old = index.publish(parsed, [Symbol("answer", "variable", Span(4, 10))])
    documents.close(parsed.uri)
    reopened = documents.open(
        uri=parsed.uri,
        language_id="nova",
        version=1,
        text="let answer = answer\n",
    )
    syntax.publish(reopened, ("module", "reopened"))

    assert index.get(parsed.uri) is None
    assert index.discard(parsed.uri) is old


def test_out_of_bounds_symbol_does_not_replace_current_snapshot() -> None:
    _, syntax, parsed = current_syntax()
    index = SymbolIndex(syntax)
    current = index.publish(parsed, [Symbol("answer", "variable", Span(4, 10))])

    with pytest.raises(SymbolError, match="symbol span is outside"):
        index.publish(parsed, [Symbol("bad", "variable", Span(0, 100))])

    assert index.get(parsed.uri) is current


def test_non_symbol_input_does_not_replace_current_snapshot() -> None:
    _, syntax, parsed = current_syntax()
    index = SymbolIndex(syntax)
    current = index.publish(parsed, [Symbol("answer", "variable", Span(4, 10))])

    with pytest.raises(SymbolError, match="must contain Symbol"):
        index.publish(parsed, [object()])  # type: ignore[list-item]

    assert index.get(parsed.uri) is current
