"""Version-bound diagnostics for language-independent tooling."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from threading import RLock

from .semantic import SemanticDatabase, SemanticError, SemanticSnapshot
from .source import Span


class DiagnosticError(ValueError):
    """Raised when diagnostics cannot be associated with current semantics."""


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A language-independent diagnostic anchored to one source span."""

    span: Span
    message: str
    severity: str = "error"
    code: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.span, Span):
            raise DiagnosticError("diagnostic span must be a Span")
        if not isinstance(self.message, str) or not self.message:
            raise DiagnosticError("diagnostic message must be a non-empty string")
        if self.severity not in {"error", "warning", "information", "hint"}:
            raise DiagnosticError("unsupported diagnostic severity")
        if self.code is not None and (not isinstance(self.code, str) or not self.code):
            raise DiagnosticError("diagnostic code must be a non-empty string or None")
        if self.source is not None and (
            not isinstance(self.source, str) or not self.source
        ):
            raise DiagnosticError("diagnostic source must be a non-empty string or None")


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    """Immutable diagnostics derived from one exact semantic snapshot."""

    semantic: SemanticSnapshot
    diagnostics: tuple[Diagnostic, ...]

    @property
    def uri(self) -> str:
        return self.semantic.uri

    @property
    def language_id(self) -> str:
        return self.semantic.language_id

    @property
    def version(self) -> int:
        return self.semantic.version


class DiagnosticStore:
    """Publish diagnostics only for the exact current semantic snapshot.

    Diagnostic computation may race with semantic recomputation, reparsing, document
    updates, close/reopen cycles, or other concurrent work. Publication therefore
    requires the exact :class:`SemanticSnapshot` used for computation to remain
    current. Publication uses the semantic database's compare-and-commit boundary so
    semantic replacement cannot slip between the identity check and diagnostic-cache
    write. This gives callers a single stale-result boundary without coupling the core
    to any language adapter.
    """

    def __init__(self, semantic: SemanticDatabase) -> None:
        self._semantic = semantic
        self._snapshots: dict[str, DiagnosticSnapshot] = {}
        self._lock = RLock()

    def get(self, uri: str) -> DiagnosticSnapshot | None:
        """Return diagnostics only when derived from current semantics."""
        semantic = self._semantic.get(uri)
        with self._lock:
            snapshot = self._snapshots.get(uri)
            if semantic is None or snapshot is None or snapshot.semantic is not semantic:
                return None
            return snapshot

    def publish(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> DiagnosticSnapshot:
        """Atomically publish deterministic diagnostics if *semantic* is current."""
        materialized = tuple(diagnostics)
        text_length = len(semantic.symbols.syntax.document.text)
        for diagnostic in materialized:
            if not isinstance(diagnostic, Diagnostic):
                raise DiagnosticError("diagnostic results must contain Diagnostic values")
            if diagnostic.span.end > text_length:
                raise DiagnosticError(
                    f"diagnostic span is outside {semantic.uri}: "
                    f"{diagnostic.span.end} > {text_length}"
                )

        ordered = tuple(
            sorted(
                materialized,
                key=lambda diagnostic: (
                    diagnostic.span.start,
                    diagnostic.span.end,
                    diagnostic.severity,
                    diagnostic.message,
                    diagnostic.code or "",
                    diagnostic.source or "",
                ),
            )
        )
        snapshot = DiagnosticSnapshot(semantic=semantic, diagnostics=ordered)

        def commit() -> DiagnosticSnapshot:
            with self._lock:
                self._snapshots[semantic.uri] = snapshot
            return snapshot

        try:
            return self._semantic.commit_if_current(semantic, commit)
        except SemanticError as exc:
            raise DiagnosticError(
                f"stale diagnostic result for {semantic.uri} at version {semantic.version}"
            ) from exc

    def discard(self, uri: str) -> DiagnosticSnapshot | None:
        """Discard any cached diagnostic snapshot for *uri*, current or stale."""
        with self._lock:
            return self._snapshots.pop(uri, None)
