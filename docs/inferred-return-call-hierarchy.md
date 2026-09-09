# Inferred Nova return types in call hierarchy

Nova call-hierarchy items now surface conservative inferred return types for uniquely resolved, unannotated functions. A function such as `fn target() { return 1; }` is presented with detail `fn target() -> Int` when the exact workspace snapshot set proves that result.

The product layer reuses the bounded Nova return inference already shared by inlay hints, hover, completion, and signature help. Explicit return annotations remain authoritative. Ambiguous declarations, conflicting returns, unsupported or unknown expressions, and inference cycles remain conservative and do not acquire an inferred result.

Prepare, incoming, and outgoing call-hierarchy responses remain gated by the exact workspace snapshot set captured for the request. Cross-file edits, close/reopen replacement, same-version semantic replacement, cancellation, or another workspace mutation invalidate the candidate rather than publishing stale inferred type information. The language-independent snapshot and query layers are unchanged.
