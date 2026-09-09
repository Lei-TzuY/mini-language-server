# Bounded Nova `if` condition semantics

The Nova product recognizes `if (...)` as control-flow syntax rather than a function call. The generic JSON-RPC, document, syntax, semantic, workspace, and diagnostic stores remain language-independent.

For an exact current semantic/workspace snapshot, the condition expression is evaluated through the existing bounded Nova expression-type pipeline. A known `Bool` condition is accepted. A known non-`Bool` condition produces deterministic `nova.condition-type` diagnostics over the trimmed condition expression. Unknown, ambiguous, malformed, or unsupported expressions remain conservative and do not produce speculative condition-type diagnostics.

Condition typing composes existing literal, exact semantic reference, uniquely resolved function-result, integer arithmetic, comparison, and logical-expression knowledge. Cross-file function results therefore invalidate and rebind with the same workspace snapshot rules as the existing diagnostics pipeline, including same-version document replacement and close/reopen transitions.

Structural scans run on the Nova trivia-masked code view while diagnostic spans remain anchored to the original source text, so `if (...)` text inside quoted strings or comments does not create control-flow diagnostics.
