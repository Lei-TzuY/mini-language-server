# Bounded Nova zero-divisor diagnostics

The Nova product reports `nova.division-by-zero` when the trivia-masked source contains an integer `/` or `%` operation whose divisor is statically known to be zero inside the bounded integer-constant grammar.

Direct signed-zero tokens `0`, `+0`, and `-0` remain supported, including forms wrapped only in balanced parentheses and whitespace such as `(0)`, `(+0)`, and `(( -0 ))`. The bounded evaluator also accepts parenthesized integer expressions composed only from decimal integer literals, unary `+`/`-`, balanced parentheses, and `+`, `-`, `*`, `/`, `%`, with normal arithmetic precedence and truncating integer division. This allows deterministic cases such as `(1 - 1)`, `(8 - 2 * 4)`, and `((7 % 7))` to be diagnosed.

The analysis remains fail-closed. Identifiers, calls, unsupported tokens, malformed expressions, and expressions whose own evaluation would divide or take a remainder by zero are not folded speculatively. An inner independently provable zero divisor can still receive its own diagnostic. Comments and quoted strings cannot manufacture diagnostics because evaluation runs over the Nova trivia-masked source view.

For a direct or purely parenthesized signed-zero divisor, the diagnostic span continues to own only the exact signed-zero token. For a compound constant expression, it owns the smallest complete evaluated expression inside its surrounding divisor parentheses. This keeps the repair range syntactically bounded while preserving outer parentheses.

For an exact current diagnostic snapshot, `textDocument/codeAction` exposes `Replace zero divisor with 1`. The edit replaces only the diagnosed divisor span. Publication follows the existing diagnostic-snapshot identity checks, so a same-version semantic replacement, document change, close/reopen transition, or other superseding publication cannot reuse an obsolete diagnostic to manufacture an edit.

The language-independent document, syntax, symbol, semantic, diagnostic, and protocol stores remain unchanged; this policy and evaluator are composed only in the final Nova product layer.
