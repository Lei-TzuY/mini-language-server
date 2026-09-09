# Inferred Nova return types in signature help

Nova `textDocument/signatureHelp` now surfaces conservative inferred return types for uniquely resolved, unannotated functions. For example, a call to `fn helper(value: Int) { return 1; }` is presented as `fn helper(value: Int) -> Int` when the exact workspace snapshot set proves that result.

The product layer reuses the bounded Nova return inference already shared by inlay hints, hover, and completion. Explicit return annotations remain authoritative. Ambiguous declarations, conflicting returns, unsupported or unknown expressions, and inference cycles remain conservative and do not acquire an inferred result.

Signature-help publication stays tied to the exact workspace snapshot set captured for the request. Cross-file changes, close/reopen replacement, same-version semantic replacement, cancellation, or another workspace mutation invalidate the candidate rather than publishing stale inferred type information.

This policy remains in the Nova product layer; the language-independent document, syntax, symbol, semantic, diagnostic, and workspace stores are unchanged.
