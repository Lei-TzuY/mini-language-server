# Nova `var` local declarations

The Nova adapter recognizes function-scoped `var` declarations alongside the existing `let` form. This matches Nova's live grammar while keeping mutability syntax in the Nova adapter rather than the language-independent document, syntax, symbol, semantic, diagnostic, or protocol stores.

A `var name` declaration publishes the same exact-snapshot `variable` symbol shape used for local tooling. References after the declaration resolve to that exact symbol when it is the unique visible local, so definition, references, prepare/rename, semantic tokens, and the downstream Nova tooling chain can consume the existing generational identity guarantees instead of maintaining a second mutable-variable index.

Mixed `let`/`var` declarations participate in the same duplicate-local and lexical-shadowing rules. A derived query remains valid only while the exact document/syntax/symbol/semantic parent chain that produced the local is current; same-version semantic replacement and close/reopen must reject stale results just as they do for `let` locals.

This slice deliberately does not add mutability rules to the generic core. Further Nova-specific differences between immutable `let` and mutable `var` remain adapter/product semantics and should be added only when they are executable and objectively testable.
