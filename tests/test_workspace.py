from __future__ import annotations

import pytest

from mini_language_server import DocumentStore, Span
from mini_language_server.semantic import Reference, SemanticDatabase
from mini_language_server.symbols import Symbol, SymbolIndex
from mini_language_server.syntax import SyntaxStore
from mini_language_server.workspace import WorkspaceIndexError, WorkspaceSymbolIndex


def snapshot(uri: str, name: str, *, version: int = 1):
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    document = documents.open(
        uri=uri, language_id="nova", version=version, text=f"{name} {name}\n"
    )
    parsed = syntax.publish(document, ("module",))
    symbols = SymbolIndex(syntax)
    declaration = Symbol(name, "function", Span(0, len(name)))
    indexed = symbols.publish(parsed, [declaration])
    semantics = SemanticDatabase(symbols)
    current = semantics.publish(
        indexed, [Reference(Span(len(name) + 1, len(name) * 2 + 1), declaration)]
    )
    return current


def test_workspace_queries_are_deterministic_across_documents() -> None:
    index = WorkspaceSymbolIndex()
    second = snapshot("file:///workspace/b.nova", "run")
    first = snapshot("file:///workspace/a.nova", "run")
    index.replace(second)
    index.replace(first)

    declarations = index.declarations("run")
    references = index.references("run")
    assert [item.uri for item in declarations] == [
        "file:///workspace/a.nova",
        "file:///workspace/b.nova",
    ]
    assert [item.uri for item in references] == [
        "file:///workspace/a.nova",
        "file:///workspace/b.nova",
    ]
    assert declarations[0].snapshot is first
    assert references[1].snapshot is second


def test_replacement_removes_all_superseded_contributions() -> None:
    index = WorkspaceSymbolIndex()
    uri = "file:///workspace/main.nova"
    old = snapshot(uri, "old")
    current = snapshot(uri, "new", version=2)
    index.replace(old)
    index.replace(current, expected=old)

    assert index.get(uri) is current
    assert index.declarations("old") == ()
    assert index.references("old") == ()
    assert index.declarations("new")[0].snapshot is current


def test_stale_compare_and_swap_cannot_overwrite_newer_snapshot() -> None:
    index = WorkspaceSymbolIndex()
    uri = "file:///workspace/main.nova"
    old = snapshot(uri, "old")
    current = snapshot(uri, "current", version=2)
    stale = snapshot(uri, "stale", version=2)
    index.replace(old)
    index.replace(current, expected=old)

    with pytest.raises(WorkspaceIndexError, match="replaced"):
        index.replace(stale, expected=old)

    assert index.get(uri) is current
    assert index.declarations("stale") == ()


def test_remove_honors_exact_snapshot_identity() -> None:
    index = WorkspaceSymbolIndex()
    uri = "file:///workspace/main.nova"
    old = snapshot(uri, "old")
    current = snapshot(uri, "current", version=2)
    index.replace(current)

    with pytest.raises(WorkspaceIndexError, match="replaced"):
        index.remove(uri, expected=old)

    assert index.remove(uri, expected=current) is current
    assert index.get(uri) is None
    assert index.declarations("current") == ()
