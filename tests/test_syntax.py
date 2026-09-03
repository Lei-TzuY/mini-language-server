from __future__ import annotations

from threading import Event, Thread

import pytest

from mini_language_server import DocumentStore
from mini_language_server.syntax import SyntaxError, SyntaxSnapshot, SyntaxStore


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


def test_document_update_cannot_split_syntax_compare_and_publish() -> None:
    documents = DocumentStore()
    syntax = SyntaxStore(documents)
    original = open_document(documents)
    publish_entered = Event()
    release_publish = Event()
    replace_started = Event()
    replace_finished = Event()
    errors: list[Exception] = []

    class BlockingSnapshots(dict[str, SyntaxSnapshot]):
        def __setitem__(self, key: str, value: SyntaxSnapshot) -> None:
            publish_entered.set()
            assert release_publish.wait(2)
            super().__setitem__(key, value)

    syntax._snapshots = BlockingSnapshots()

    def publish() -> None:
        try:
            syntax.publish(original, ("version", 1))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    publisher = Thread(target=publish, name="syntax-publish")
    publisher.start()
    assert publish_entered.wait(2)

    def replace() -> None:
        replace_started.set()
        try:
            documents.replace(
                uri=original.uri,
                version=2,
                text="let answer = 43\n",
            )
            replace_finished.set()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    replacer = Thread(target=replace, name="document-replace")
    replacer.start()
    assert replace_started.wait(2)
    assert not replace_finished.wait(0.05)

    release_publish.set()
    publisher.join(2)
    replacer.join(2)

    assert not publisher.is_alive()
    assert not replacer.is_alive()
    assert errors == []
    assert replace_finished.is_set()
    current = documents.get(original.uri)
    assert current is not None
    assert current.version == 2
    assert syntax.get(original.uri) is None
