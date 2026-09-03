from __future__ import annotations

from threading import Thread

import pytest

from mini_language_server import (
    DocumentStore,
    RequestCancelled,
    RequestError,
    RequestTracker,
    StaleRequest,
)


def test_cancelled_request_rejects_checkpoint() -> None:
    tracker = RequestTracker(DocumentStore())
    context = tracker.start(7)

    assert tracker.cancel(7) is True
    assert context.cancelled is True
    assert tracker.is_current(context) is False
    with pytest.raises(RequestCancelled):
        tracker.checkpoint(context)


def test_unknown_and_finished_cancellation_is_harmless() -> None:
    tracker = RequestTracker(DocumentStore())

    assert tracker.cancel("missing") is False
    context = tracker.start("request")
    assert tracker.finish(context) is True
    assert tracker.cancel("request") is False
    assert tracker.finish(context) is False


def test_request_is_bound_to_exact_document_generation() -> None:
    documents = DocumentStore()
    first = documents.open(
        uri="file:///main.nova", language_id="nova", version=1, text="let x = 1"
    )
    tracker = RequestTracker(documents)
    context = tracker.start(1, uri=first.uri)

    documents.replace(uri=first.uri, version=2, text="let x = 2")

    assert tracker.is_current(context) is False
    with pytest.raises(StaleRequest):
        tracker.checkpoint(context)


def test_close_and_reopen_same_uri_invalidates_old_request() -> None:
    documents = DocumentStore()
    first = documents.open(
        uri="file:///main.nova", language_id="nova", version=1, text="first"
    )
    tracker = RequestTracker(documents)
    context = tracker.start("hover", uri=first.uri)

    documents.close(first.uri)
    documents.open(uri=first.uri, language_id="nova", version=1, text="second")

    with pytest.raises(StaleRequest):
        tracker.checkpoint(context)


def test_duplicate_active_id_is_rejected_but_id_can_be_reused_after_finish() -> None:
    tracker = RequestTracker(DocumentStore())
    first = tracker.start(3)

    with pytest.raises(RequestError):
        tracker.start(3)

    assert tracker.finish(first) is True
    second = tracker.start(3)
    assert second is not first
    assert tracker.is_current(second) is True


def test_late_finish_cannot_remove_newer_reused_id_generation() -> None:
    tracker = RequestTracker(DocumentStore())
    first = tracker.start("same")
    assert tracker.finish(first) is True
    second = tracker.start("same")

    assert tracker.finish(first) is False
    assert tracker.is_current(second) is True
    assert len(tracker) == 1


def test_concurrent_cancel_is_visible_at_checkpoint() -> None:
    tracker = RequestTracker(DocumentStore())
    context = tracker.start(42)

    worker = Thread(target=lambda: tracker.cancel(42))
    worker.start()
    worker.join()

    with pytest.raises(RequestCancelled):
        tracker.checkpoint(context)


def test_request_validation_rejects_ambiguous_ids_and_missing_documents() -> None:
    tracker = RequestTracker(DocumentStore())

    for invalid_id in (None, True, ""):
        with pytest.raises(RequestError):
            tracker.start(invalid_id)

    with pytest.raises(RequestError):
        tracker.start(1, uri="file:///missing.nova")
