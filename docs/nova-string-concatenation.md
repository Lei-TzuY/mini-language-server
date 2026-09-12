# Bounded Nova string concatenation typing

The Nova product layer recognizes `String + String` as a bounded `String` expression. The rule participates in the same exact-snapshot expression pipeline used by argument validation, return validation, explicit-local validation, local/function-result inference, and downstream hover/completion/inlay-hint surfaces that consume those inferred types.

The implementation deliberately remains conservative:

- only binary `+` is string concatenation;
- both operands must resolve to exact bounded `String` types from the current semantic/workspace snapshot;
- string subtraction, multiplication, division, and remainder remain unknown;
- mixed `String`/numeric `+` remains unknown rather than guessing a coercion;
- quoted operator characters are ignored by structural operator scans through Nova's trivia-aware code view;
- same-family `Int` and `UInt` arithmetic keeps its existing behavior.

Cross-file function-call operands are re-evaluated against the exact current workspace snapshot, so changing a callee's result type invalidates concatenation-derived results instead of allowing a stale type to publish.
