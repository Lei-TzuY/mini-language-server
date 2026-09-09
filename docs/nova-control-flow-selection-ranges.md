# Nova control-flow selection ranges

The Nova product extends negotiated `textDocument/selectionRange` results with structural control-flow parents derived from the exact current semantic snapshot.

For a token inside a complete `if`, `else if`, direct `else`, or `while` block, selection grows through the innermost containing control-flow construct before the enclosing function and whole document. Nested constructs are ordered from smallest to largest so repeated selection remains deterministic.

Structural detection runs over the Nova adapter's trivia-masked code view. Keywords, parentheses, and braces inside line comments, block comments, or quoted strings therefore cannot manufacture selection parents, while returned ranges retain the original source offsets.

Publication remains guarded by the same exact `SemanticSnapshot` identity as the existing lexical selection-range capability. A same-version semantic replacement, close/reopen, cancellation, or document change cannot publish a result owned by an older parent snapshot. The generic document, syntax, symbol, semantic, and request stores remain language-independent; these structural ownership rules stay in Nova product composition.
