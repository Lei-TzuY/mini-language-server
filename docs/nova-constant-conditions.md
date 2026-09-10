# Nova constant-condition diagnostics

The final Nova product reports `nova.constant-condition` when the exact source expression of an `if` or `while` condition reduces, after trivia-safe trimming and redundant outer-parenthesis removal, to the literal `true` or `false`.

This slice is intentionally conservative. It does not evaluate aliases, comparisons, logical expressions, function calls, or other expressions even when a compiler could fold them. The purpose is to surface only conditions whose constant value is unambiguous from the current source while preserving exact diagnostic spans.

The same literal proof now feeds bounded `nova.unreachable-code` diagnostics for structurally dead regions. A constant-false `if` body, a constant-false `while` body, and a direct `else { ... }` body attached to a constant-true `if` are marked unreachable at the exact non-whitespace body span and carry the standard LSP `Unnecessary` tag. The analysis deliberately does not treat `while (true)` as making later code unreachable, does not fold nonliteral expressions, and does not speculate across `else if` chains.

If an older return/control-flow unreachable diagnostic already subsumes a constant-dead region, the broader existing diagnostic owns the span. Conversely, a constant-dead body subsumes narrower unreachable diagnostics wholly contained inside that body. This keeps publication deterministic and avoids overlapping duplicate `nova.unreachable-code` reports for the same dead source.

Exact-snapshot code actions now add structural repairs where deleting an entire control-flow fragment is semantics-preserving without CFG speculation. A constant-false `while` can be removed as a whole, a constant-false `if` can be removed as a whole only when it has no direct `else`, and a direct `else { ... }` attached to a constant-true `if` can be removed as a whole. Constant-false `if` statements with an `else` and constant-false `else if` arms deliberately keep only the existing body-level `Remove unreachable code` repair because deleting their surrounding syntax would require a rewrite rather than a safe bounded deletion.

Structural repairs are tied to the exact current `DiagnosticSnapshot` and its exact semantic parent. A superseded same-version diagnostic cannot produce an edit, `didChange` recomputes availability from the new document snapshot, and close/reopen cannot reuse an action tied to an older snapshot chain.

Diagnostics are produced from the current `SemanticSnapshot` publication path. Same-version semantic replacement therefore republishes against the new semantic identity, and close/reopen cannot retain a diagnostic tied to an older document/syntax/symbol/semantic chain.

Examples:

```nova
fn main(flag: Bool) {
    if (true) { }
    while (((false))) { let dead = 1; }
    if (false) { let also_dead = 2; }
    if (true) { let live = 3; } else { let dead_else = 4; }
    if (flag) { } // no constant-condition diagnostic
}
```

Literal conditions are diagnosed at the exact `true` and `false` spans. The dead statements in the constant-false bodies and direct constant-true `else` body are separately diagnosed as unreachable. Comments and quoted strings are masked by the Nova adapter before structural scanning, so text such as `"if (false)"` or `// while (true)` is ignored.
