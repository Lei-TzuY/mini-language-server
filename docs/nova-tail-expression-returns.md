# Nova function tail-expression returns

The Nova product recognizes one bounded final block expression as a function result when validating an explicitly annotated function. This follows the implemented Nova grammar where a block may end in one expression without a semicolon and that expression is the block's value.

The language server first keeps the existing explicit-return and structurally complete `if`/`else if`/`else` proof. When that proof does not already guarantee a value return, it isolates the final top-level source region after the last top-level statement semicolon. Nested braces, parentheses, and brackets do not contribute statement boundaries. The candidate is then typed only through the existing exact semantic/workspace expression pipeline.

When the tail type is known and matches the function annotation, the inherited `nova.missing-return` diagnostic is suppressed. When the type is known and conflicts, the missing-return diagnostic is replaced by one exact-span `nova.return-type` diagnostic on the tail expression. The existing return-type quick fix can therefore repair a tail mismatch without a separate action protocol.

Supported tail knowledge is intentionally the same bounded knowledge already available to explicit return expressions: literals including `()`, exact parameter/local references, uniquely resolved same-file or cross-file calls with bounded result types, and expression forms supported by the current arithmetic/comparison/logical layers. Unsupported or ambiguous expressions remain unknown and cannot suppress `nova.missing-return`.

All results remain attached to the exact semantic snapshot and, for workspace-derived call types, the exact workspace snapshot set. Document changes, close/reopen, same-version semantic replacement, stale workspace publication, and request cancellation therefore retain the same fail-closed behavior as the existing return diagnostics and code actions.
