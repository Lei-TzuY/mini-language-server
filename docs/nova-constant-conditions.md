# Nova constant-condition diagnostics

The final Nova product reports `nova.constant-condition` when the exact source expression of an `if` or `while` condition reduces, after trivia-safe trimming and redundant outer-parenthesis removal, to the literal `true` or `false`.

This slice is intentionally conservative. It does not evaluate aliases, comparisons, logical expressions, function calls, or other expressions even when a compiler could fold them. The purpose is to surface only conditions whose constant value is unambiguous from the current source while preserving exact diagnostic spans.

Diagnostics are produced from the current `SemanticSnapshot` publication path. Same-version semantic replacement therefore republishes against the new semantic identity, and close/reopen cannot retain a diagnostic tied to an older document/syntax/symbol/semantic chain.

Examples:

```nova
fn main(flag: Bool) {
    if (true) { }
    while (((false))) { }
    if (flag) { } // no constant-condition diagnostic
}
```

The two literal conditions are diagnosed at the exact `true` and `false` spans. Comments and quoted strings are masked by the Nova adapter before structural scanning, so text such as `"if (false)"` or `// while (true)` is ignored.
