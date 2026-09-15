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

## Rule for new work

New completion behavior must be added as a transformation inside
`completion_pipeline.py` or as a pure helper called by that pipeline. It must
not introduce another serial final-product subclass. Unary-plus behavior must
remain in `unary_plus.py` (or pure helpers it calls) rather than being split
back into separate product subclasses.

The remaining historical product layers are acknowledged migration debt, not a
template for continued expansion. Future consolidation should proceed one
domain at a time, preserving executable behavior and stale-snapshot rejection
while removing superseded runtime layers.
