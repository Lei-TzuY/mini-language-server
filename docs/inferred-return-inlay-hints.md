# Inferred Nova return inlay hints

The Nova product layer surfaces a type inlay hint on an unannotated function declaration only when the existing bounded function-result inference can prove one exact `Int`, `String`, or `Bool` result.

Each published inferred-return hint is directly actionable: its LSP `textEdits` inserts the same exact ` -> Type` annotation at the function declaration, so clients that support applying inlay-hint edits can materialize the inferred type without recomputing it independently.

The hint is intentionally conservative:

- explicit `-> Type` annotations are never duplicated;
- duplicate/ambiguous workspace declarations do not receive an inferred hint;
- conflicting, unknown, unsupported, or cyclic return inference produces no hint;
- cross-file call chains reuse the exact current workspace snapshots rather than cached text guesses;
- the edit and displayed label are derived together from the same inferred type and zero-width declaration insertion range;
- publication shares the existing `textDocument/inlayHint` cancellation, exact semantic snapshot, and exact workspace snapshot-set guards, so same-version replacement or close/reopen invalidation cannot publish a stale annotation edit.

This policy remains in the Nova product composition. The generic document, syntax, symbol, semantic, and workspace stores remain language-independent.
