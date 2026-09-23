"""Version-bound diagnostics for language-independent tooling."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from threading import RLock
from typing import TypeVar

from .semantic import SemanticDatabase, SemanticError, SemanticSnapshot
from .source import Span


class DiagnosticError(ValueError):
    """Raised when diagnostics cannot be associated with current semantics."""


DIAGNOSTIC_TAG_VALUES = {"unnecessary": 1, "deprecated": 2}


@dataclass(frozen=True, slots=True)
class DiagnosticRelatedInformation:
    """A source location related to one diagnostic and its exact semantic parent."""

    uri: str
    span: Span
    message: str
    semantic: SemanticSnapshot | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri:
            raise DiagnosticError(
                "diagnostic related-information URI must be a non-empty string"
            )
        if not isinstance(self.span, Span):
            raise DiagnosticError(
                "diagnostic related-information span must be a Span"
            )
        if not isinstance(self.message, str) or not self.message:
            raise DiagnosticError(
                "diagnostic related-information message must be a non-empty string"
            )
        if self.semantic is not None:
            if not isinstance(self.semantic, SemanticSnapshot):
                raise DiagnosticError(
                    "diagnostic related-information semantic must be "
                    "a SemanticSnapshot or None"
                )
            if self.semantic.uri != self.uri:
                raise DiagnosticError(
                    "diagnostic related-information semantic URI must match its URI"
                )


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A language-independent diagnostic anchored to one source span."""

    span: Span
    message: str
    severity: str = "error"
    code: str | None = None
    source: str | None = None
    tags: tuple[str, ...] = ()
    related_information: tuple[DiagnosticRelatedInformation, ...] = ()

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
        if not isinstance(self.tags, tuple):
            raise DiagnosticError("diagnostic tags must be a tuple")
        if len(set(self.tags)) != len(self.tags):
            raise DiagnosticError("diagnostic tags must be unique")
        if any(tag not in DIAGNOSTIC_TAG_VALUES for tag in self.tags):
            raise DiagnosticError("unsupported diagnostic tag")
        if not isinstance(self.related_information, tuple):
            raise DiagnosticError("diagnostic related information must be a tuple")
        if any(
            not isinstance(item, DiagnosticRelatedInformation)
            for item in self.related_information
        ):
            raise DiagnosticError(
                "diagnostic related information must contain "
                "DiagnosticRelatedInformation values"
            )
        if len(set(self.related_information)) != len(self.related_information):
            raise DiagnosticError("diagnostic related information must be unique")


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

    @property
    def related_semantics(self) -> tuple[SemanticSnapshot, ...]:
        """Return exact cross-location semantic parents in deterministic URI order."""
        by_uri: dict[str, SemanticSnapshot] = {}
        for diagnostic in self.diagnostics:
            for related in diagnostic.related_information:
                if related.semantic is None:
                    continue
                previous = by_uri.get(related.uri)
                if previous is not None and previous is not related.semantic:
                    raise DiagnosticError(
                        "diagnostic snapshot contains conflicting related semantics"
                    )
                by_uri[related.uri] = related.semantic
        return tuple(by_uri[uri] for uri in sorted(by_uri))


_T = TypeVar("_T")


class DiagnosticStore:
    """Publish diagnostics only for the exact current semantic snapshot."""

    def __init__(self, semantic: SemanticDatabase) -> None:
        self._semantic = semantic
        self._snapshots: dict[str, DiagnosticSnapshot] = {}
        self._lock = RLock()

    def get(self, uri: str) -> DiagnosticSnapshot | None:
        """Return diagnostics only when every exact semantic parent is current."""
        semantic = self._semantic.get(uri)
        with self._lock:
            snapshot = self._snapshots.get(uri)
            if semantic is None or snapshot is None or snapshot.semantic is not semantic:
                return None
        for related in snapshot.related_semantics:
            if self._semantic.get(related.uri) is not related:
                return None
        return snapshot

    def get_primary_current(
        self, semantic: SemanticSnapshot
    ) -> DiagnosticSnapshot | None:
        """Return a primary-current snapshot solely for deterministic recomputation.

        Related semantic parents may already be stale. Callers must rebuild any
        diagnostics that depend on those parents before publishing or rendering.
        """
        if not isinstance(semantic, SemanticSnapshot):
            raise DiagnosticError(
                "primary-current lookup requires a SemanticSnapshot"
            )
        if self._semantic.get(semantic.uri) is not semantic:
            return None
        with self._lock:
            snapshot = self._snapshots.get(semantic.uri)
            if snapshot is None or snapshot.semantic is not semantic:
                return None
            return snapshot

    def commit_if_current(
        self, snapshot: DiagnosticSnapshot, commit: Callable[[], _T]
    ) -> _T:
        """Run *commit* atomically while *snapshot* remains current diagnostics."""
        if not isinstance(snapshot, DiagnosticSnapshot):
            raise DiagnosticError("current snapshot guard requires a DiagnosticSnapshot")
        if not callable(commit):
            raise DiagnosticError("snapshot commit must be callable")

        def guarded_commit() -> _T:
            with self._lock:
                current = self._snapshots.get(snapshot.uri)
                if current is not snapshot:
                    raise DiagnosticError(
                        "stale diagnostic snapshot for "
                        f"{snapshot.uri} at version {snapshot.version}"
                    )
                return commit()

        semantics = self._unique_semantics(
            (snapshot.semantic, *snapshot.related_semantics)
        )
        try:
            return self._commit_semantics_if_current(
                semantics,
                guarded_commit,
            )
        except SemanticError as exc:
            raise DiagnosticError(
                f"stale diagnostic snapshot for {snapshot.uri} at version {snapshot.version}"
            ) from exc

    def commit_all_if_current(
        self,
        snapshots: Iterable[DiagnosticSnapshot],
        commit: Callable[[], _T],
    ) -> _T:
        """Run *commit* while every supplied diagnostic snapshot remains exact-current."""
        materialized = tuple(snapshots)
        if not callable(commit):
            raise DiagnosticError("snapshot commit must be callable")
        if any(not isinstance(snapshot, DiagnosticSnapshot) for snapshot in materialized):
            raise DiagnosticError("snapshot set guard requires DiagnosticSnapshot values")
        uris = tuple(snapshot.uri for snapshot in materialized)
        if len(set(uris)) != len(uris):
            raise DiagnosticError("snapshot set guard requires unique diagnostic URIs")

        semantics = self._unique_semantics(
            tuple(
                semantic
                for snapshot in materialized
                for semantic in (snapshot.semantic, *snapshot.related_semantics)
            )
        )

        def guarded_commit() -> _T:
            with self._lock:
                for snapshot in materialized:
                    if self._snapshots.get(snapshot.uri) is not snapshot:
                        raise DiagnosticError(
                            "stale diagnostic snapshot for "
                            f"{snapshot.uri} at version {snapshot.version}"
                        )
                return commit()

        try:
            return self._commit_semantics_if_current(semantics, guarded_commit)
        except SemanticError as exc:
            raise DiagnosticError("stale diagnostic snapshot set") from exc

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
            for related in diagnostic.related_information:
                if related.semantic is None:
                    if related.uri != semantic.uri:
                        raise DiagnosticError(
                            "cross-URI diagnostic related information requires "
                            "an exact semantic snapshot"
                        )
                    related_semantic = semantic
                else:
                    related_semantic = related.semantic
                    if related.uri == semantic.uri and related_semantic is not semantic:
                        raise DiagnosticError(
                            "same-URI diagnostic related information must reference "
                            "the primary semantic snapshot"
                        )
                    if self._semantic.get(related.uri) is not related_semantic:
                        raise DiagnosticError(
                            "diagnostic related-information semantic is stale"
                        )
                related_length = len(
                    related_semantic.symbols.syntax.document.text
                )
                if related.span.end > related_length:
                    raise DiagnosticError(
                        f"diagnostic related-information span is outside "
                        f"{related.uri}: {related.span.end} > {related_length}"
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
                    diagnostic.tags,
                    tuple(
                        (
                            related.uri,
                            related.span.start,
                            related.span.end,
                            related.message,
                        )
                        for related in diagnostic.related_information
                    ),
                ),
            )
        )
        snapshot = DiagnosticSnapshot(semantic=semantic, diagnostics=ordered)

        def commit() -> DiagnosticSnapshot:
            with self._lock:
                self._snapshots[semantic.uri] = snapshot
            return snapshot

        semantics = self._unique_semantics(
            (semantic, *snapshot.related_semantics)
        )
        try:
            return self._commit_semantics_if_current(semantics, commit)
        except SemanticError as exc:
            raise DiagnosticError(
                f"stale diagnostic result for {semantic.uri} at version {semantic.version}"
            ) from exc

    @staticmethod
    def _unique_semantics(
        semantics: tuple[SemanticSnapshot, ...],
    ) -> tuple[SemanticSnapshot, ...]:
        """Deduplicate exact semantic parents and reject conflicting URI identities."""
        by_uri: dict[str, SemanticSnapshot] = {}
        for semantic in semantics:
            previous = by_uri.get(semantic.uri)
            if previous is not None and previous is not semantic:
                raise DiagnosticError(
                    "diagnostic semantic parents contain conflicting URI identities"
                )
            by_uri[semantic.uri] = semantic
        return tuple(by_uri[uri] for uri in sorted(by_uri))

    def _commit_semantics_if_current(
        self,
        semantics: tuple[SemanticSnapshot, ...],
        commit: Callable[[], _T],
    ) -> _T:
        """Run one callback while every semantic parent remains exact-current."""

        def guard_at(index: int) -> _T:
            if index == len(semantics):
                return commit()
            semantic = semantics[index]
            return self._semantic.commit_if_current(
                semantic,
                lambda: guard_at(index + 1),
            )

        return guard_at(0)

    def discard(self, uri: str) -> DiagnosticSnapshot | None:
        """Discard any cached diagnostic snapshot for *uri*, current or stale."""
        with self._lock:
            return self._snapshots.pop(uri, None)
