from threading import Event, Thread, current_thread

import pytest

from mini_language_server.documents import DocumentError, DocumentStore
from mini_language_server.source import SourceText


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


def test_concurrent_changes_cannot_publish_lower_version_after_higher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DocumentStore()
    uri = "file:///race.nova"
    store.open(uri=uri, language_id="nova", version=1, text="abc")
    slow_entered = Event()
    release_slow = Event()
    fast_started = Event()
    original = SourceText.span_from_range

    def blocking_span(self, start, end):
        if current_thread().name == "slow-v2":
            slow_entered.set()
            assert release_slow.wait(2)
        return original(self, start, end)

    monkeypatch.setattr(SourceText, "span_from_range", blocking_span)
    errors: list[Exception] = []

    def change(version: int, replacement: str) -> None:
        try:
            store.apply_changes(
                uri=uri,
                version=version,
                changes=[
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "text": replacement,
                    }
                ],
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    slow = Thread(target=change, args=(2, "2"), name="slow-v2")
    slow.start()
    assert slow_entered.wait(2)

    def run_fast() -> None:
        fast_started.set()
        change(3, "3")

    fast = Thread(target=run_fast, name="fast-v3")
    fast.start()
    assert fast_started.wait(2)
    release_slow.set()
    slow.join(2)
    fast.join(2)

    assert not slow.is_alive()
    assert not fast.is_alive()
    assert errors == []
    current = store.get(uri)
    assert current is not None
    assert current.version == 3
    assert current.text == "3bc"


def test_close_cannot_race_with_change_and_resurrect_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DocumentStore()
    uri = "file:///close-race.nova"
    store.open(uri=uri, language_id="nova", version=1, text="abc")
    change_entered = Event()
    release_change = Event()
    close_started = Event()
    original = SourceText.span_from_range

    def blocking_span(self, start, end):
        if current_thread().name == "change":
            change_entered.set()
            assert release_change.wait(2)
        return original(self, start, end)

    monkeypatch.setattr(SourceText, "span_from_range", blocking_span)
    errors: list[Exception] = []

    def change() -> None:
        try:
            store.apply_changes(
                uri=uri,
                version=2,
                changes=[
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "text": "x",
                    }
                ],
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    changer = Thread(target=change, name="change")
    changer.start()
    assert change_entered.wait(2)

    def close() -> None:
        close_started.set()
        try:
            store.close(uri)
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    closer = Thread(target=close, name="close")
    closer.start()
    assert close_started.wait(2)
    release_change.set()
    changer.join(2)
    closer.join(2)

    assert not changer.is_alive()
    assert not closer.is_alive()
    assert errors == []
    assert store.get(uri) is None

def test_incremental_positions_can_use_utf8_code_units() -> None:
    store = DocumentStore(position_encoding="utf-8")
    uri = "file:///emoji.nova"
    store.open(uri=uri, language_id="nova", version=1, text="a😀b\n")

    updated = store.apply_changes(
        uri=uri,
        version=2,
        changes=[
            {
                "range": {
                    "start": {"line": 0, "character": 1},
                    "end": {"line": 0, "character": 5},
                },
                "text": "X",
            }
        ],
    )

    assert updated.text == "aXb\n"


def test_utf8_incremental_change_rejects_split_code_point_without_commit() -> None:
    store = DocumentStore(position_encoding="utf-8")
    uri = "file:///emoji.nova"
    original = store.open(uri=uri, language_id="nova", version=1, text="a😀b")

    with pytest.raises(DocumentError, match="UTF-8 code point"):
        store.apply_changes(
            uri=uri,
            version=2,
            changes=[
                {
                    "range": {
                        "start": {"line": 0, "character": 3},
                        "end": {"line": 0, "character": 5},
                    },
                    "text": "X",
                }
            ],
        )

    assert store.get(uri) == original


def test_position_encoding_cannot_change_after_document_open() -> None:
    store = DocumentStore()
    store.open(uri="file:///a.nova", language_id="nova", version=1, text="abc")

    with pytest.raises(DocumentError, match="while documents are open"):
        store.set_position_encoding("utf-8")

def test_matching_snapshot_guard_rejects_new_matching_document() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=1,
        text="a",
    )
    captured = (first,)

    store.open(
        uri="file:///workspace/b.nova",
        language_id="nova",
        version=1,
        text="b",
    )

    with pytest.raises(DocumentError, match="matching open document set changed"):
        store.commit_matching_if_current(
            captured,
            lambda document: document.uri.startswith("file:///workspace/"),
            lambda: True,
        )


def test_matching_snapshot_guard_ignores_nonmatching_document_addition() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=1,
        text="a",
    )
    captured = (first,)

    store.open(
        uri="file:///outside/b.nova",
        language_id="nova",
        version=1,
        text="b",
    )

    assert store.commit_matching_if_current(
        captured,
        lambda document: document.uri.startswith("file:///workspace/"),
        lambda: "committed",
    ) == "committed"


def test_matching_snapshot_guard_rejects_replaced_matching_snapshot() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=1,
        text="a",
    )
    store.replace(uri=first.uri, version=2, text="new")

    with pytest.raises(DocumentError, match="stale document snapshot"):
        store.commit_matching_if_current(
            (first,),
            lambda document: document.uri.startswith("file:///workspace/"),
            lambda: True,
        )

def test_incremental_positions_can_use_utf32_code_point_units() -> None:
    store = DocumentStore(position_encoding="utf-32")
    uri = "file:///emoji-utf32.nova"
    store.open(uri=uri, language_id="nova", version=1, text="a😀b\n")

    updated = store.apply_changes(
        uri=uri,
        version=2,
        changes=[
            {
                "range": {
                    "start": {"line": 0, "character": 1},
                    "end": {"line": 0, "character": 2},
                },
                "text": "X",
            }
        ],
    )

    assert updated.text == "aXb\n"

def test_rename_many_rekeys_open_snapshots_without_changing_content_or_version() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=7,
        text="fn a() {}\n",
    )
    second = store.open(
        uri="file:///workspace/b.nova",
        language_id="nova",
        version=3,
        text="fn b() {}\n",
    )

    moved = store.rename_many(
        (
            ("file:///workspace/a.nova", "file:///workspace/c.nova"),
            ("file:///workspace/b.nova", "file:///workspace/d.nova"),
        )
    )

    assert tuple(previous for previous, _ in moved) == (first, second)
    assert store.get(first.uri) is None
    assert store.get(second.uri) is None
    current_c = store.get("file:///workspace/c.nova")
    current_d = store.get("file:///workspace/d.nova")
    assert current_c is not None
    assert current_d is not None
    assert (current_c.version, current_c.text) == (7, "fn a() {}\n")
    assert (current_d.version, current_d.text) == (3, "fn b() {}\n")
    assert current_c is not first
    assert current_d is not second


def test_rename_many_supports_atomic_uri_swaps() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=1,
        text="A",
    )
    second = store.open(
        uri="file:///workspace/b.nova",
        language_id="nova",
        version=2,
        text="B",
    )

    store.rename_many(
        (
            ("file:///workspace/a.nova", "file:///workspace/b.nova"),
            ("file:///workspace/b.nova", "file:///workspace/a.nova"),
        )
    )

    swapped_a = store.get("file:///workspace/a.nova")
    swapped_b = store.get("file:///workspace/b.nova")
    assert swapped_a is not None and swapped_a.text == second.text
    assert swapped_b is not None and swapped_b.text == first.text
    assert swapped_a.version == second.version
    assert swapped_b.version == first.version


def test_rename_many_destination_collision_is_atomic() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=1,
        text="A",
    )
    occupied = store.open(
        uri="file:///workspace/occupied.nova",
        language_id="nova",
        version=9,
        text="occupied",
    )

    with pytest.raises(DocumentError, match="destination already open"):
        store.rename_many(
            (("file:///workspace/a.nova", "file:///workspace/occupied.nova"),)
        )

    assert store.get(first.uri) is first
    assert store.get(occupied.uri) is occupied


def test_rename_many_duplicate_target_is_atomic() -> None:
    store = DocumentStore()
    first = store.open(
        uri="file:///workspace/a.nova",
        language_id="nova",
        version=1,
        text="A",
    )
    second = store.open(
        uri="file:///workspace/b.nova",
        language_id="nova",
        version=1,
        text="B",
    )

    with pytest.raises(DocumentError, match="destinations must be unique"):
        store.rename_many(
            (
                (first.uri, "file:///workspace/c.nova"),
                (second.uri, "file:///workspace/c.nova"),
            )
        )

    assert store.get(first.uri) is first
    assert store.get(second.uri) is second
    assert store.get("file:///workspace/c.nova") is None
