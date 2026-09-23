# Nova inline completion

The final Nova product supports the LSP 3.18 `textDocument/inlineCompletion` request as a bounded presentation of the same lexical/type-aware completion evidence used by ordinary completion. The protocol surface is implemented in the existing completion consolidation layer; it does not add another serial product subclass or recursively issue a hidden `textDocument/completion` request.

A client opts in by advertising `textDocument.inlineCompletion` during `initialize`. The server then advertises `inlineCompletionProvider: {}`. Requests require a valid LSP 3.18 inline-completion context with trigger kind `1` (invoked) or `2` (automatic).

This first slice intentionally handles plain Nova identifier prefixes only. The request resolves the exact current semantic snapshot, reuses the shared typed completion candidate helper, filters candidates by the identifier prefix at the cursor, ranks parameters/locals ahead of constants and functions using the existing completion classification, and returns same-line replacement ranges in the negotiated UTF-8/UTF-16/UTF-32 position units. Invoked requests may return multiple deterministic candidates; automatic requests publish only the highest-ranked candidate.

Specialized completion contexts remain owned by their existing flows. Inline completion therefore fails closed for `Type::member` prefixes and direct numeric-conversion operand contexts rather than returning generic lexical candidates that could contradict the dedicated member/type-directed completion logic. Empty prefixes also return no inline item to avoid noisy whole-workspace ghost text.

Inline completion owns its own request generation. Cancellation checkpoints, document staleness, semantic freshness, and complete workspace snapshot identity are checked before publication. A cross-file declaration mutation that occurs after candidates are captured invalidates the whole inline result with `Content modified`; cancellation uses the normal request id and returns `Request cancelled`. No internal request id or nested completion request is created.

The slice deliberately returns plain string insertions rather than snippets or commands. Ordinary completion retains snippet negotiation, insert/replace edits, completion-list defaults, lazy resolve, member completion, and type-directed operand completion. Future inline-completion expansion should reuse those semantics through shared pure helpers rather than duplicate protocol handlers.
