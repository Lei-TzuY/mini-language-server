# Bounded Nova zero-divisor diagnostics

The Nova product reports `nova.division-by-zero` when the trivia-masked source contains an integer `/` or `%` operation whose divisor is the statically literal zero token `0`, `+0`, or `-0`. The same diagnostic applies when that exact signed-zero literal is wrapped only in balanced parentheses and whitespace, including forms such as `(0)`, `(+0)`, and `(( -0 ))`.

The diagnostic is deliberately conservative. It does not attempt constant folding, alias evaluation, or speculative value analysis, and comments or quoted strings cannot manufacture a diagnostic. Parenthesized compound expressions such as `(0 + 1)` and structurally incomplete parenthesized expressions are not treated as zero divisors. The diagnostic span owns only the exact signed-zero literal in the original source, not its surrounding parentheses.

For an exact current diagnostic snapshot, `textDocument/codeAction` exposes `Replace zero divisor with 1`. The edit replaces only the diagnosed divisor span, preserving any surrounding parentheses. Publication follows the existing diagnostic-snapshot identity checks, so a same-version semantic replacement, document change, close/reopen transition, or other superseding publication cannot reuse an obsolete diagnostic to manufacture an edit.

The language-independent document, syntax, symbol, semantic, diagnostic, and protocol stores remain unchanged; this policy is composed only in the final Nova product layer.
