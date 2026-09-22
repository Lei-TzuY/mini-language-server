# Product architecture consolidation

The repository historically shipped each product capability by defining another
`NovaProductLanguageServer` subclass that imported the previous final class.
That preserved behavior locally, but made product semantics depend on a long,
order-sensitive inheritance chain.

Completion publication is the first consolidated domain. Function snippets,
negotiated insert/replace edits, semantic classification and ranking, and
identifier-prefix filtering now execute in one
`completion_pipeline.NovaProductLanguageServer` boundary over the earlier
product base. One exact semantic/workspace publication gate protects the
combined transformation.

Unary-plus support is the second consolidated feature boundary. Diagnostic
detection and its exact-snapshot quick fix now live in
`unary_plus.NovaProductLanguageServer`, replacing the former two-layer
diagnostic/action pair. The feature still delegates diagnostic publication and
code-action collection through the preceding product base, preserving ordering
and stale-document rejection.

UInt typing is the third consolidated domain. Constant and explicit-result
typing, same-family arithmetic/comparisons, and explicit Int/UInt conversions
now share `uint_types.NovaProductLanguageServer`. The combined boundary keeps
conversion recursion ahead of numeric fallback without spending three serial
product subclasses.

Explicit Int/UInt conversion diagnostics and their published-diagnostic-bound
quick fix form the fourth consolidated boundary in
`uint_conversion.NovaProductLanguageServer`. Detection, publication, and repair
share one product layer while the repair still rejects stale or reconstructed
diagnostics.

Bounded expression typing is the fifth consolidated domain. Arithmetic and String
concatenation, equality/ordering comparisons, and logical Boolean composition now
share `expression_types.NovaProductLanguageServer`. The boundary preserves the
existing exact-snapshot and cycle-safe inference path while making operator
precedence explicit: `||` and `&&` compose comparisons, comparisons compose
arithmetic operands, and unary `!` binds to its operand rather than swallowing a
following comparison. The former arithmetic/comparison/logical runtime subclasses
are removed.

## Rule for new work

New completion behavior must be added as a transformation inside
`completion_pipeline.py` or as a pure helper called by that pipeline. It must
not introduce another serial final-product subclass. Unary-plus behavior must
remain in `unary_plus.py` (or pure helpers it calls) rather than being split
back into separate product subclasses. Numeric conversion diagnostics and actions
must remain together in `uint_conversion.py` or pure helpers called by it. Bounded
expression semantics must remain in `expression_types.py` (or pure helpers it calls)
rather than returning to serial arithmetic/comparison/logical product subclasses.

The remaining historical product layers are acknowledged migration debt, not a
template for continued expansion. Future consolidation should proceed one
domain at a time, preserving executable behavior and stale-snapshot rejection
while removing superseded runtime layers.
