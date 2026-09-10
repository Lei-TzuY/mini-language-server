# Bounded Nova zero-divisor diagnostics

The Nova product reports `nova.division-by-zero` when the trivia-masked source contains an integer `/` or `%` operation whose divisor is the statically literal zero token `0`, `+0`, or `-0`.

The diagnostic is deliberately conservative. It does not attempt constant folding, alias evaluation, or speculative value analysis, and comments or quoted strings cannot manufacture a diagnostic. The diagnostic span owns only the zero divisor literal in the original source.

For an exact current diagnostic snapshot, `textDocument/codeAction` exposes `Replace zero divisor with 1`. The edit replaces only the diagnosed divisor span. Publication follows the existing diagnostic-snapshot identity checks, so a same-version semantic replacement, document change, close/reopen transition, or other superseding publication cannot reuse an obsolete diagnostic to manufacture an edit.

The language-independent document, syntax, symbol, semantic, diagnostic, and protocol stores remain unchanged; this policy is composed only in the final Nova product layer.
