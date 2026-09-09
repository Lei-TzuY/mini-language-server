# Inferred Nova return type surfaces

The Nova product layer surfaces a type inlay hint on an unannotated function declaration only when the existing bounded function-result inference can prove one exact `Int`, `String`, or `Bool` result. The same conservative result is also exposed in `textDocument/hover` for a uniquely resolved function declaration or call, so editor navigation does not hide type knowledge that the server already proved.

Each published inferred-return hint is directly actionable: its LSP `textEdits` inserts the same exact ` -> Type` annotation immediately after the declaration's parameter list, so clients that support applying inlay-hint edits can materialize the inferred type without recomputing it independently. Hover remains read-only and renders the inferred result as part of the bounded Nova function signature.

The surfaces are intentionally conservative:

- explicit `-> Type` annotations are never duplicated or replaced;
- duplicate/ambiguous workspace declarations do not receive an inferred hint or inferred function hover signature;
- conflicting, unknown, unsupported, or cyclic return inference produces no inferred type;
- cross-file call chains reuse the exact current workspace snapshots rather than cached text guesses;
- the inlay edit and displayed label are derived together from the same inferred type and zero-width post-parameter insertion range;
- inlay publication shares the existing `textDocument/inlayHint` cancellation, exact semantic snapshot, and exact workspace snapshot-set guards;
- hover independently pins the exact workspace snapshot set before publishing, so same-version replacement or close/reopen invalidation cannot publish a stale inferred signature.

The inlay insertion anchor is discovered from the trivia-masked Nova code view, preserving source offsets while ignoring parentheses inside comments and quoted strings.

This policy remains in the Nova product composition. The generic document, syntax, symbol, semantic, and workspace stores remain language-independent.
