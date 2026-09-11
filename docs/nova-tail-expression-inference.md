# Nova tail-expression result inference

The final Nova product can infer a bounded result type for an unannotated function from its final block expression when the function contains no explicit `return` statement.

The tail must be a structurally valid top-level final expression and its type must already be derivable through the exact semantic/workspace inference pipeline. This includes bounded literals, exact typed references, uniquely resolved function calls, and the existing bounded arithmetic/comparison/logical expression composition. The inferred result is therefore available to downstream exact-snapshot hover, completion/local typing, call diagnostics, return validation, and other consumers of function-call result typing.

The rule is intentionally conservative. Functions with both explicit returns and a tail expression are not inferred by this layer; ambiguous or unsupported tails, incomplete bodies, recursive cycles, and stale workspace snapshots remain unknown. Existing explicit-return and bare-return inference behavior remains unchanged when no tail expression competes with it.

Recursive inference carries the exact declaration snapshot identity through the resolving set, so a tail-call cycle cannot manufacture a result type. Workspace consumers still publish only through the existing exact-snapshot compare-and-commit guards; same-version replacement, close/reopen, cancellation, and document changes therefore retain their normal stale-result semantics.
