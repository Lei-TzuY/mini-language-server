from __future__ import annotations

from threading import Event, Thread

import pytest

from mini_language_server import DocumentStore, Span
from mini_language_server.semantic import (
    Reference,
    SemanticDatabase,
    SemanticError,
    SemanticSnapshot,
)
from mini_language_server.symbols import Symbol, SymbolIndex
from mini_language_server.syntax import SyntaxStore


def current_symbols():
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
    return documents, syntax, index, parsed, symbols, declaration


def test_publish_supports_definition_and_reference_queries() -> None:
    _, _, index, _, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    reference = Reference(Span(13, 19), declaration)

    snapshot = database.publish(symbols, [reference])

    assert database.get(symbols.uri) is snapshot
    assert snapshot.symbols is symbols
    assert snapshot.definition_at(4) is declaration
    assert snapshot.definition_at(15) is declaration
    assert snapshot.definition_at(12) is None
    assert snapshot.references_to(declaration) == (Span(13, 19),)
    assert snapshot.references_to(declaration, include_declaration=True) == (
        Span(4, 10),
        Span(13, 19),
    )


def test_reindexing_same_syntax_invalidates_semantics() -> None:
    _, _, index, parsed, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    old = database.publish(symbols, [Reference(Span(13, 19), declaration)])

    replacement = Symbol("answer", "variable", Span(4, 10))
    newer_symbols = index.publish(parsed, [replacement])

    assert newer_symbols.syntax is symbols.syntax
    assert newer_symbols is not symbols
    assert database.get(symbols.uri) is None
    with pytest.raises(SemanticError, match="stale semantic result"):
        database.publish(symbols, old.references)


def test_document_update_hides_semantic_snapshot() -> None:
    documents, _, index, _, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    database.publish(symbols, [Reference(Span(13, 19), declaration)])

    documents.replace(uri=symbols.uri, version=2, text="let answer = 43\n")

    assert index.get(symbols.uri) is None
    assert database.get(symbols.uri) is None


def test_structurally_equal_symbol_from_other_generation_is_rejected() -> None:
    _, _, index, _, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    equal_but_distinct = Symbol(declaration.name, declaration.kind, declaration.span)

    with pytest.raises(SemanticError, match="target is not part"):
        database.publish(symbols, [Reference(Span(13, 19), equal_but_distinct)])

    assert database.get(symbols.uri) is None


def test_invalid_reference_batch_does_not_replace_current_snapshot() -> None:
    _, _, index, _, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    current = database.publish(symbols, [Reference(Span(13, 19), declaration)])

    with pytest.raises(SemanticError, match="reference span is outside"):
        database.publish(symbols, [Reference(Span(13, 100), declaration)])

    assert database.get(symbols.uri) is current


def test_overlapping_reference_batch_is_rejected_atomically() -> None:
    _, _, index, _, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    current = database.publish(symbols, [Reference(Span(13, 19), declaration)])

    with pytest.raises(SemanticError, match="must not overlap"):
        database.publish(
            symbols,
            [Reference(Span(12, 16), declaration), Reference(Span(15, 19), declaration)],
        )

    assert database.get(symbols.uri) is current


def test_reference_order_is_deterministic() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    document = documents.open(
        uri="file:///workspace/main.nova",
        language_id="nova",
        version=1,
        text="let answer = answer + answer\n",
    )
    parsed = syntax.publish(document, ("module",))
    index = SymbolIndex(syntax)
    declaration = Symbol("answer", "variable", Span(4, 10))
    symbols = index.publish(parsed, [declaration])
    database = SemanticDatabase(index)

    snapshot = database.publish(
        symbols,
        [Reference(Span(22, 28), declaration), Reference(Span(13, 19), declaration)],
    )

    assert snapshot.references_to(declaration) == (Span(13, 19), Span(22, 28))


def test_query_rejects_symbol_not_owned_by_snapshot() -> None:
    _, _, index, _, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    snapshot = database.publish(symbols, [Reference(Span(13, 19), declaration)])
    outsider = Symbol("answer", "variable", Span(4, 10))

    with pytest.raises(SemanticError, match="target is not part"):
        snapshot.references_to(outsider)

    with pytest.raises(SemanticError, match="non-negative integer"):
        snapshot.definition_at(-1)


def test_reindex_cannot_split_semantic_compare_and_publish() -> None:
    _, _, index, parsed, symbols, declaration = current_symbols()
    database = SemanticDatabase(index)
    publish_entered = Event()
    release_publish = Event()
    reindex_started = Event()
    reindex_finished = Event()
    errors: list[Exception] = []

    class BlockingSnapshots(dict[str, SemanticSnapshot]):
        def __setitem__(self, key: str, value: SemanticSnapshot) -> None:
            publish_entered.set()
            assert release_publish.wait(2)
            super().__setitem__(key, value)

    database._snapshots = BlockingSnapshots()

    def publish_semantics() -> None:
        try:
            database.publish(symbols, [Reference(Span(13, 19), declaration)])
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    publisher = Thread(target=publish_semantics, name="semantic-publish")
    publisher.start()
    assert publish_entered.wait(2)

    replacement = Symbol("answer", "variable", Span(4, 10))

    def reindex() -> None:
        reindex_started.set()
        try:
            index.publish(parsed, [replacement])
            reindex_finished.set()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    reindexing = Thread(target=reindex, name="symbol-reindex")
    reindexing.start()
    assert reindex_started.wait(2)
    assert not reindex_finished.wait(0.05)

    release_publish.set()
    publisher.join(2)
    reindexing.join(2)

    assert not publisher.is_alive()
    assert not reindexing.is_alive()
    assert errors == []
    assert reindex_finished.is_set()
    current = index.get(symbols.uri)
    assert current is not None
    assert current is not symbols
    assert database.get(symbols.uri) is None
