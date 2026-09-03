"""Thread-safe request cancellation and stale-result suppression primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, RLock
from typing import Any

from .documents import Document, DocumentStore


class RequestError(RuntimeError):
    """Base class for request lifecycle failures."""


class RequestCancelled(RequestError):
    """Raised when a request has been cancelled."""


class StaleRequest(RequestError):
    """Raised when a request is bound to an obsolete document generation."""


@dataclass(slots=True)
class RequestContext:
    """Identity-bearing lifecycle token for one in-flight request generation."""

    request_id: str | int
    document: Document | None = None
    _cancelled: Event = field(default_factory=Event, repr=False)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()


class RequestTracker:
    """Track in-flight requests safely across cancellation and ID reuse.

    Each context is identity-bearing. A late completion from an older generation
    cannot remove a newer request that reused the same JSON-RPC id.
    """

    def __init__(self, documents: DocumentStore) -> None:
        self._documents = documents
        self._active: dict[str | int, RequestContext] = {}
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._active)

    def start(self, request_id: Any, *, uri: str | None = None) -> RequestContext:
        if not isinstance(request_id, str | int) or isinstance(request_id, bool):
            raise RequestError("request id must be a string or integer")
        if isinstance(request_id, str) and not request_id:
            raise RequestError("request id must not be empty")

        document = None
        if uri is not None:
            if not isinstance(uri, str) or not uri:
                raise RequestError("document uri must be a non-empty string")
            document = self._documents.get(uri)
            if document is None:
                raise RequestError(f"document is not open: {uri}")

        context = RequestContext(request_id=request_id, document=document)
        with self._lock:
            if request_id in self._active:
                raise RequestError(f"request already active: {request_id!r}")
            self._active[request_id] = context
        return context

    def cancel(self, request_id: Any) -> bool:
        """Cancel an active request; unknown/finished ids are harmless."""
        with self._lock:
            context = self._active.get(request_id)
            if context is None:
                return False
            context._cancelled.set()
            return True

    def finish(self, context: RequestContext) -> bool:
        """Finish exactly this request generation.

        Returns ``False`` if the context is already finished or a newer request
        has reused the same id.
        """
        with self._lock:
            if self._active.get(context.request_id) is not context:
                return False
            del self._active[context.request_id]
            return True

    def checkpoint(self, context: RequestContext) -> None:
        """Reject cancelled work and results computed from stale documents."""
        if context.cancelled:
            raise RequestCancelled(f"request cancelled: {context.request_id!r}")
        document = context.document
        if document is not None and self._documents.get(document.uri) is not document:
            raise StaleRequest(
                f"request document snapshot is stale: {document.uri}@{document.version}"
            )

    def is_current(self, context: RequestContext) -> bool:
        try:
            self.checkpoint(context)
        except RequestError:
            return False
        with self._lock:
            return self._active.get(context.request_id) is context
