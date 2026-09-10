# Bounded Nova local declaration validation

The final Nova product mirrors the current Nova grammar for the bounded local-declaration forms it recognizes.

`let name = expression;` remains the immutable initialized binding form. An uninitialized `let name: Type;` is rejected with `nova.uninitialized-let` because Nova requires immutable locals to have initialization evidence at declaration time.

`var name: Type;` is accepted as the mutable declaration form whose first value may be supplied by a later assignment. The explicit type is mandatory when the declaration has no initializer, so `var name;` is rejected with `nova.untyped-var`. Initialized `var name = expression;` continues to use the existing exact-snapshot inferred-type and assignment-validation path.

For a typed uninitialized `var`, the adapter reports `nova.uninitialized-read` on exact semantic references that lack a preceding resolved assignment that is structurally visible from the read's lexical brace scope. An assignment in the same scope, or an enclosing scope of that read, is bounded definite-initialization evidence. An assignment confined to a nested conditional or loop body no longer initializes a later read outside that body; a later read in the same nested body is initialized normally. This remains deliberately conservative and does not attempt full control-flow joins such as proving both arms of an `if`/`else` assign the binding.

The diagnostics are computed from the trivia-masked Nova code view, so declaration-like text and braces in comments and strings are ignored while source offsets remain exact. A typed uninitialized `let` offers a deterministic quick fix that changes only the declaration keyword to `var`; an untyped `var` intentionally has no speculative type-insertion fix.

Like the rest of the Nova product composition, these diagnostics and repairs are published only through the exact current semantic/document snapshot. The generic document, syntax, symbol, semantic, diagnostic, and protocol stores remain language-independent.