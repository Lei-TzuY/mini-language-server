# Nova Unit type tooling

The Nova product recognizes the implemented Nova `Unit` surface type and its sole literal, `()`, in the existing exact-snapshot bounded type pipeline.

`()` is treated as `Unit` when validating typed call arguments, explicit local initializers, mutable assignments, and explicit function returns. A uniquely resolved same-file or cross-file function declared with `-> Unit` also contributes `Unit` as its call-result type to those same bounded consumers.

The feature reuses the existing semantic/workspace snapshot identities and publication guards. It does not add a parallel type store or leak Nova-specific rules into the language-independent document, syntax, symbol, semantic, diagnostic, or protocol layers. Ambiguous calls and unsupported compound expressions remain conservative.

`Unit` is intentionally not added to the value-return requirement used by `nova.missing-return`: a `-> Unit` function may fall through, matching Nova's implemented grammar and semantic contract. An explicit `return ();` is nevertheless type-checked, so using `()` in a non-`Unit` return position or passing it to a non-`Unit` parameter produces the existing deterministic mismatch diagnostics.
