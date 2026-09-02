from __future__ import annotations

import pytest

from mini_language_server import DocumentStore
from mini_language_server.syntax import SyntaxError, SyntaxStore


def open_document(store: DocumentStore, *, version: int = 1):
    return store.open(
        uri="file:///workspace/main.nova",
        language_id="nova",
        version=version,
        text="let answer = 42\n",
    )


def test_publish_and_get_current_snapshot() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    document = open_document(documents)
    tree = ("module", ("let", "answer", 42))

    snapshot = syntax.publish(document, tree)

    assert syntax.get(document.uri) is snapshot
    assert snapshot.document is document
    assert snapshot.uri == document.uri
    assert snapshot.language_id == "nova"
    assert snapshot.version == 1
    assert snapshot.tree is tree


def test_document_update_hides_stale_snapshot() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    original = open_document(documents)
    syntax.publish(original, ("version", 1))

    current = documents.replace(uri=original.uri, version=2, text="let answer = 43\n")

    assert current.version == 2
    assert syntax.get(original.uri) is None


def test_stale_publish_is_rejected_after_document_update() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    original = open_document(documents)
    documents.replace(uri=original.uri, version=2, text="let answer = 43\n")

    with pytest.raises(SyntaxError, match="stale syntax result"):
        syntax.publish(original, ("version", 1))

    assert syntax.get(original.uri) is None


def test_close_hides_snapshot_and_rejects_late_publish() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    document = open_document(documents)
    syntax.publish(document, ("module",))

    documents.close(document.uri)

    assert syntax.get(document.uri) is None
    with pytest.raises(SyntaxError, match="stale syntax result"):
        syntax.publish(document, ("late",))


def test_reopen_same_uri_and_version_does_not_resurrect_old_snapshot() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    original = open_document(documents)
    old_snapshot = syntax.publish(original, ("old",))
    documents.close(original.uri)

    reopened = open_document(documents)

    assert reopened == original
    assert reopened is not original
    assert syntax.get(reopened.uri) is None
    assert syntax.discard(reopened.uri) is old_snapshot


def test_current_publish_replaces_older_cached_result() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    original = open_document(documents)
    old_snapshot = syntax.publish(original, ("version", 1))
    current = documents.replace(uri=original.uri, version=2, text="let answer = 43\n")

    new_snapshot = syntax.publish(current, ("version", 2))

    assert new_snapshot is not old_snapshot
    assert syntax.get(current.uri) is new_snapshot
    assert syntax.discard(current.uri) is new_snapshot
    assert syntax.get(current.uri) is None
