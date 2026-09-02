import pytest

from mini_language_server.documents import DocumentError, DocumentStore


def test_open_stores_initial_snapshot() -> None:
    store = DocumentStore()
    document = store.open(
        uri="file:///workspace/main.nova",
        language_id="nova",
        version=1,
        text="let answer = 42\n",
    )
    assert store.get(document.uri) == document
    assert document.version == 1
    assert document.text == "let answer = 42\n"


def test_replace_requires_strictly_newer_version() -> None:
    store = DocumentStore()
    store.open(uri="file:///a.nova", language_id="nova", version=3, text="old")

    updated = store.replace(uri="file:///a.nova", version=4, text="new")

    assert updated.version == 4
    assert updated.text == "new"


@pytest.mark.parametrize("version", [3, 2])
def test_replace_rejects_duplicate_or_stale_version(version: int) -> None:
    store = DocumentStore()
    store.open(uri="file:///a.nova", language_id="nova", version=3, text="current")

    with pytest.raises(DocumentError, match="stale document version"):
        store.replace(uri="file:///a.nova", version=version, text="stale")

    assert store.get("file:///a.nova").text == "current"


def test_close_removes_snapshot() -> None:
    store = DocumentStore()
    store.open(uri="file:///a.nova", language_id="nova", version=1, text="x")

    closed = store.close("file:///a.nova")

    assert closed.text == "x"
    assert store.get("file:///a.nova") is None


def test_duplicate_open_is_rejected_without_replacing_snapshot() -> None:
    store = DocumentStore()
    store.open(uri="file:///a.nova", language_id="nova", version=1, text="first")

    with pytest.raises(DocumentError, match="already open"):
        store.open(uri="file:///a.nova", language_id="nova", version=2, text="second")

    assert store.get("file:///a.nova").text == "first"
