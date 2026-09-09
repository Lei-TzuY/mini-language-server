# Bounded Nova loop-control semantics

Nova recognizes `break` and `continue` as executable loop-control statements rather than ordinary identifiers. The generic document, syntax, symbol, semantic, diagnostic, and workspace stores remain language-independent; placement policy stays in the Nova product layer.

A `break` or `continue` is valid when its exact trivia-masked source position is enclosed by a structurally complete `while (...) { ... }` body. This includes nested `if` and other brace blocks inside that loop. A loop-control statement elsewhere in a function produces deterministic `nova.invalid-loop-control` over the exact statement token plus an immediately following semicolon when present. Text inside comments and quoted strings remains masked and cannot manufacture loop-control diagnostics.

The Nova adapter also removes `break` and `continue` from unresolved-name diagnostics, so legal loop control does not appear as an ordinary name-resolution failure. Invalid placement is instead owned by the dedicated loop-control diagnostic.

For an exact current `nova.invalid-loop-control` diagnostic, `textDocument/codeAction` exposes `Remove invalid loop-control statement`. The edit deletes only the diagnosed statement. The action verifies diagnostic object ownership in the exact current `DiagnosticSnapshot` and remains behind the existing request cancellation and diagnostic/workspace compare-and-commit publication gates. Same-version reanalysis, `didChange`, close/reopen, or cancellation therefore cannot publish a repair derived from a stale diagnostic.
