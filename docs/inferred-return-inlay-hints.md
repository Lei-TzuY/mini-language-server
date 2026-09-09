# Inferred Nova return inlay hints

The Nova product layer surfaces a type inlay hint on an unannotated function declaration only when the existing bounded function-result inference can prove one exact `Int`, `String`, or `Bool` result.

The hint is intentionally conservative:

- explicit `-> Type` annotations are never duplicated;
- duplicate/ambiguous workspace declarations do not receive an inferred hint;
- conflicting, unknown, unsupported, or cyclic return inference produces no hint;
- cross-file call chains reuse the exact current workspace snapshots rather than cached text guesses;
- publication shares the existing `textDocument/inlayHint` cancellation, exact semantic snapshot, and exact workspace snapshot-set guards, so same-version replacement or close/reopen invalidation cannot publish stale type hints.

This policy remains in the Nova product composition. The generic document, syntax, symbol, semantic, and workspace stores remain language-independent.
