# Nova reference semantic tokens and binding modifiers

The final Nova product extends semantic tokens beyond declaration-only symbol snapshots. Exact semantic references receive the token type of their resolved target, so function calls, parameter uses, and local-variable uses are highlighted from the same `SemanticSnapshot` that powers definition and references.

## Negotiated modifiers

When the client advertises semantic-token modifier support, the server exposes the supported intersection in deterministic legend order:

- `declaration` marks Nova function, parameter, and local declarations.
- `readonly` marks `let` local declarations and every semantic reference resolved to that exact immutable local.
- `modification` marks resolved semantic references that are assignment targets.

`var` locals remain mutable and therefore do not receive `readonly`. Parameters are not labeled readonly because the current Nova mutability contract only rejects assignment to `let` locals. An attempted assignment to a `let` local is still a write in editor semantics, so its target token carries both `readonly` and `modification` even when diagnostics reject the assignment. Unsupported modifiers are not advertised and never set in the bitset.

Assignment classification uses the Nova trivia-masked code view and only applies to references already resolved by the exact semantic snapshot. Identifiers inside comments or quoted strings therefore cannot become modification tokens, and equality operators are not mistaken for assignment.

Clients that support semantic tokens but no modifiers still receive reference tokens with a zero modifier bitset.

## Snapshot and range guarantees

The implementation stays in the Nova product layer; the language-independent `Symbol`, `SymbolSnapshot`, and semantic-token encoder remain unchanged. Reference tokens are derived from the exact current `SemanticSnapshot`, while the existing semantic-token request path retains its workspace snapshot publication gate, cancellation checkpoints, and same-version replacement rejection.

Full and range requests share the same token construction. Range requests include only declarations and references intersecting the requested half-open span. Existing full/delta support therefore consumes the same deterministic reference-aware token stream.
