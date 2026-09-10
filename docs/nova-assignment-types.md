# Bounded Nova typed-assignment validation

The final Nova product validates ordinary assignments whose left-hand side resolves through the exact current semantic snapshot to an explicitly typed local or function parameter.

When the right-hand expression has a bounded `Int`, `String`, or `Bool` type and disagrees with that explicit type, the server publishes `nova.assignment-type` on the exact right-hand expression span. Existing exact-snapshot expression inference is reused, including bounded arithmetic, comparison, logical, reference, and uniquely resolved function-call results. Untyped targets and unknown or ambiguous right-hand expressions remain conservative and produce no assignment-type diagnostic.

`textDocument/codeAction` offers a deterministic replacement literal matching the target's explicit type. The action requires the exact current diagnostic object and document snapshot, so same-version semantic replacement, document change, close/reopen, or other superseding publication cannot turn a stale diagnostic into an edit.

The generic document, syntax, symbol, semantic, diagnostic, and protocol stores remain language-independent; assignment policy is composed only in the final Nova product layer.
