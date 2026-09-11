# Nova Unit type tooling

The Nova product recognizes the implemented Nova `Unit` surface type and its sole literal, `()`, in the existing exact-snapshot bounded type pipeline.

`()` is treated as `Unit` when validating typed call arguments, explicit local initializers, mutable assignments, and explicit function returns. A uniquely resolved same-file or cross-file function declared with `-> Unit` also contributes `Unit` as its call-result type to those same bounded consumers.

Matching `Unit` operands now participate in the existing bounded comparison pipeline for `==` and `!=`, producing `Bool` just like matching `Int`, `String`, and `Bool` operands. The comparison layer preserves the exact `()` literal before ordinary grouping-parenthesis unwrapping, while Unit-typed semantic references and uniquely resolved Unit-returning calls reuse the existing exact semantic/workspace type knowledge. Unit ordering comparisons, mixed Unit/non-Unit equality, chained comparisons, ambiguous calls, and unknown operands remain conservative rather than manufacturing a type.

Unannotated function-result inference also treats direct bare `return;` statements as `Unit` evidence. A function whose observed return statements are all bare returns can therefore contribute `Unit` through the existing exact-workspace hover/completion/signature/inlay and type-diagnostic consumers. If any value-bearing return is present alongside a bare return, result inference stays unknown; the bounded analysis does not attempt control-flow proof that would justify collapsing mixed paths. A function with no return statements is likewise not inferred as `Unit` merely because it can fall through.

The feature reuses the existing semantic/workspace snapshot identities and publication guards. It does not add a parallel type store or leak Nova-specific rules into the language-independent document, syntax, symbol, semantic, diagnostic, or protocol layers. Ambiguous calls, mixed bare/value returns, and unsupported compound expressions remain conservative.

`Unit` is intentionally not added to the value-return requirement used by `nova.missing-return`: a `-> Unit` function may fall through, matching Nova's implemented grammar and semantic contract. An explicit `return ();` is nevertheless type-checked, so using `()` in a non-`Unit` return position or passing it to a non-`Unit` parameter produces the existing deterministic mismatch diagnostics.

When a current exact diagnostic expects `Unit`, `textDocument/codeAction` offers a deterministic quick fix that replaces the diagnosed argument, return expression, explicit-local initializer, or assignment value with `()`. The repair is emitted only for the current diagnostic object bound to the current semantic/document snapshot, so same-version semantic replacement, document changes, close/reopen, and request cancellation continue to use the existing stale-result guards rather than publishing an edit from an obsolete analysis.
