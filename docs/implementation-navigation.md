# Exact-snapshot implementation navigation

The final Nova product advertises `implementationProvider: true` and handles `textDocument/implementation` through the same exact semantic binding and workspace publication path as definition navigation.

For the currently supported Nova surface, function declarations are concrete implementations. A uniquely resolved same-file or cross-file function reference therefore resolves to that function's declaration span. Ambiguous workspace function names remain conservative and return no implementation rather than guessing.

Implementation results inherit the existing generational-identity guarantees: publication is rejected when the captured semantic/workspace snapshot set is no longer exact, including same-version replacement and close/reopen races. Requests also preserve the existing cancellation checkpoints before final publication.

This capability stays in product composition and does not add Nova-specific behavior to the language-independent document, syntax, symbol, semantic, diagnostic, or request-tracking stores.
