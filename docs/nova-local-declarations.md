# Bounded Nova local declaration validation

The final Nova product mirrors the current Nova grammar for the bounded local-declaration forms it recognizes.

`let name = expression;` remains the immutable initialized binding form. An uninitialized `let name: Type;` is rejected with `nova.uninitialized-let` because Nova requires immutable locals to have initialization evidence at declaration time.

`var name: Type;` is accepted as the mutable declaration form whose first value may be supplied by a later assignment. The explicit type is mandatory when the declaration has no initializer, so `var name;` is rejected with `nova.untyped-var`. Initialized `var name = expression;` continues to use the existing exact-snapshot inferred-type and assignment-validation path.

For a typed uninitialized `var`, the adapter also reports `nova.uninitialized-read` on exact semantic references that occur lexically before the first resolved assignment to that same local symbol. This is deliberately bounded definite-initialization evidence: reads before the first assignment are certainly invalid, while the adapter makes no claim about control-flow-sensitive initialization after that first assignment.

The diagnostics are computed from the trivia-masked Nova code view, so declaration-like text in comments and strings is ignored while source offsets remain exact. A typed uninitialized `let` offers a deterministic quick fix that changes only the declaration keyword to `var`; an untyped `var` intentionally has no speculative type-insertion fix.

Like the rest of the Nova product composition, these diagnostics and repairs are published only through the exact current semantic/document snapshot. The generic document, syntax, symbol, semantic, diagnostic, and protocol stores remain language-independent.