# Bounded Nova typed-assignment validation

The final Nova product validates ordinary assignments whose left-hand side resolves through the exact current semantic snapshot to a function parameter or local variable with a bounded known type.

Explicit `Int`, `String`, and `Bool` annotations remain authoritative. For unannotated locals, the same exact-snapshot local-type inference already used by hover, completion, inlay hints, and argument typing may establish a bounded target type from literal, function-call, arithmetic, comparison, or logical initializers. Unknown, ambiguous, cyclic, or otherwise unsupported initializer types remain conservative and do not create assignment diagnostics.

When the right-hand expression has a bounded `Int`, `String`, or `Bool` type and disagrees with the target type, the server publishes `nova.assignment-type` on the exact right-hand expression span. Existing exact-snapshot expression inference is reused, including bounded arithmetic, comparison, logical, reference, and uniquely resolved function-call results.

`textDocument/codeAction` offers a deterministic replacement literal matching the target type. The action requires the exact current diagnostic object and document snapshot, so same-version semantic replacement, document change, close/reopen, cross-file invalidation, or other superseding publication cannot turn a stale diagnostic into an edit.

The generic document, syntax, symbol, semantic, diagnostic, and protocol stores remain language-independent; assignment policy is composed only in the final Nova product layer.
