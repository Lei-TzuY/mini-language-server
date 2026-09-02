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


def test_incremental_changes_are_applied_sequentially() -> None:
    store = DocumentStore()
    uri = "file:///a.nova"
    store.open(uri=uri, language_id="nova", version=1, text="alpha\nbeta\n")

    updated = store.apply_changes(
        uri=uri,
        version=2,
        changes=[
            {
                "range": {
                    "start": {"line": 0, "character": 5},
                    "end": {"line": 0, "character": 5},
                },
                "text": "!",
            },
            {
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 1, "character": 4},
                },
                "text": "BETA",
            },
        ],
    )

    assert updated.version == 2
    assert updated.text == "alpha!\nBETA\n"


def test_incremental_positions_use_utf16_code_units() -> None:
    store = DocumentStore()
    uri = "file:///emoji.nova"
    store.open(uri=uri, language_id="nova", version=1, text="a😀b\n")

    updated = store.apply_changes(
        uri=uri,
        version=2,
        changes=[
            {
                "range": {
                    "start": {"line": 0, "character": 1},
                    "end": {"line": 0, "character": 3},
                },
                "text": "X",
            }
        ],
    )

    assert updated.text == "aXb\n"


def test_incremental_change_rejects_surrogate_split_without_commit() -> None:
    store = DocumentStore()
    uri = "file:///emoji.nova"
    original = store.open(uri=uri, language_id="nova", version=1, text="a😀b")

    with pytest.raises(DocumentError, match="surrogate pair"):
        store.apply_changes(
            uri=uri,
            version=2,
            changes=[
                {
                    "range": {
                        "start": {"line": 0, "character": 2},
                        "end": {"line": 0, "character": 3},
                    },
                    "text": "X",
                }
            ],
        )

    assert store.get(uri) == original


def test_multiple_changes_are_atomic_when_later_range_is_invalid() -> None:
    store = DocumentStore()
    uri = "file:///a.nova"
    original = store.open(uri=uri, language_id="nova", version=1, text="abc")

    with pytest.raises(DocumentError, match="outside the line"):
        store.apply_changes(
            uri=uri,
            version=2,
            changes=[
                {
                    "range": {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 1},
                    },
                    "text": "z",
                },
                {
                    "range": {
                        "start": {"line": 0, "character": 99},
                        "end": {"line": 0, "character": 99},
                    },
                    "text": "!",
                },
            ],
        )

    assert store.get(uri) == original


def test_full_replacement_can_appear_in_content_change_batch() -> None:
    store = DocumentStore()
    uri = "file:///a.nova"
    store.open(uri=uri, language_id="nova", version=1, text="old")

    updated = store.apply_changes(
        uri=uri,
        version=2,
        changes=[{"text": "new"}],
    )

    assert updated.text == "new"


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
