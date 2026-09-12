# Nova inlay hint resolve

The final Nova product negotiates LSP `inlayHint/resolve` only when the client advertises supported properties through `textDocument.inlayHint.resolveSupport.properties`.

The bounded implementation supports `tooltip` and `textEdits`. When `textEdits` resolution is supported, editable type hints omit that payload from the initial `textDocument/inlayHint` response and restore the exact captured edit only during `inlayHint/resolve`. Tooltips are likewise produced lazily from the exact captured hint.

Every resolve token is bound to the exact `SemanticSnapshot` and workspace snapshot set that produced the hint. Same-version semantic replacement, workspace replacement, `didChange`, close/reopen, or any other lineage change rejects the stale resolve request with `Content modified` instead of recomputing against newer state. Resolve also checkpoints cancellation at the final publication boundary.

Clients that advertise inlay hints without supported resolve properties retain the existing eager `inlayHintProvider: true` behavior.