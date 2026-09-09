# Inferred Nova return types in completion

Nova completion details surface conservative inferred return types for unique unannotated functions. For example, `fn helper() { return 1; }` is presented as `fn helper() -> Int` when the exact workspace snapshot set proves that result.

The product layer reuses the bounded return inference shared with hover and inlay hints. Explicit annotations always win. Ambiguous declarations, conflicting returns, unsupported/unknown expressions, and cycles remain conservative and do not acquire inferred return details.

Completion publication remains tied to the exact workspace snapshot set used for the request. Cross-file edits, close/reopen replacement, same-version semantic replacement, or another workspace mutation invalidate the candidate rather than publishing a stale inferred signature. Existing completion cancellation checkpoints remain authoritative.

This behavior is Nova-specific product policy layered above the language-independent document, syntax, symbol, semantic, diagnostic, and workspace stores.
