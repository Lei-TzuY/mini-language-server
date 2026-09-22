# Nova constant-condition diagnostics

The final Nova product reports `nova.constant-condition` when the exact source expression of an `if` or `while` condition is provably constant. Direct `true`/`false` literals remain supported, and the proof now composes unary `!`, `&&`, `||`, Bool equality/inequality, and integer `==`, `!=`, `<`, `<=`, `>`, `>=` when both operands are bounded integer constant expressions. Integer operands reuse the existing decimal/unary-minus/arithmetic constant grammar, including normal arithmetic precedence and truncating division.

The evaluator follows Nova precedence rather than textual shortcuts: logical OR composes logical AND, comparisons compose bounded constant operands, and unary `!` binds to its operand. It remains fail-closed for identifiers, aliases, function calls, Strings, malformed expressions, divide/remainder by zero, unsupported unary `+`, chained comparisons, or any operand whose value is not independently proven. All diagnostics keep the exact post-trim/post-grouping source span.

The same product-independent constant proof also feeds bounded control-flow termination and value-return analysis. A provably true `if` can establish a closed function path from its reachable body even without an `else`; a provably false `if` can use only its reachable `else` / `else if` branch. A provably true `while` with no reachable `break` also closes fallthrough through divergence, so `nova.missing-return` is suppressed when every reachable path either returns a value or is proven not to fall through. Unknown conditions and loops with reachable or possibly reachable breaks remain conservative. The same facts let unreachable-code analysis mark source following proven termination boundaries.

The same constant proof feeds bounded `nova.unreachable-code` diagnostics for structurally dead regions. A constant-false `if` body, a constant-false `while` body, and a direct `else { ... }` body attached to a constant-true `if` are marked unreachable at the exact non-whitespace body span and carry the standard LSP `Unnecessary` tag. A provably true `while` is additionally treated as non-fallthrough only when its body contains no reachable `break` targeting that loop. Breaks hidden in proven-dead branches or owned by nested loops do not make the outer loop fall through; a break under unknown control flow does, preserving fail-closed behavior. Unknown loop conditions remain conservative.

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

Literal conditions retain their exact literal spans; folded expressions are diagnosed at the exact proven expression span after redundant outer grouping is removed. The dead statements in the constant-false bodies and direct constant-true `else` body are separately diagnosed as unreachable. Comments and quoted strings are masked by the Nova adapter before structural scanning, so text such as `"if (false)"` or `// while (true)` is ignored.
