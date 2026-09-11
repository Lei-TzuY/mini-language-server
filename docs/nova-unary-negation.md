# Nova unary negation typing

The Nova product layer treats unary `-` as a bounded Int-preserving expression when its operand resolves to `Int` from the exact current semantic/workspace snapshot.

This matches Nova's grammar, where the only prefix unary operators are `!` and `-`. Unary `+` is not a Nova expression and therefore must not manufacture an `Int` result inside the language server.

The bounded negation result composes with the existing consumers for call-argument validation, explicit return validation, explicit-local initializer validation, and unannotated function-result inference. Parenthesized and nested negations reuse the same exact-snapshot expression path, including uniquely resolved cross-file call results.

Unsupported or unknown operands remain conservative. In particular, unary `+`, non-Int negation, ambiguous calls, and stale workspace results do not publish derived type knowledge.
