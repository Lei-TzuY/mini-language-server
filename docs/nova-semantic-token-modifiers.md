# Nova reference semantic tokens and mutability modifiers

The final Nova product extends semantic tokens beyond declaration-only symbol snapshots. Exact semantic references now receive the token type of their resolved target, so function calls, parameter uses, and local-variable uses are highlighted from the same `SemanticSnapshot` that powers definition and references.

## Negotiated modifiers

When the client advertises semantic-token modifier support, the server exposes the supported intersection in a deterministic legend order:

- `declaration` marks Nova function, parameter, and local declarations.
- `readonly` marks `let` local declarations and every semantic reference resolved to that exact immutable local.

`var` locals remain mutable and therefore do not receive `readonly`. Parameters are not labeled readonly because the current Nova mutability contract only rejects assignment to `let` locals. Unsupported modifiers are not advertised and never set in the bitset.

Clients that support semantic tokens but no modifiers still receive the new reference tokens with a zero modifier bitset.

## Snapshot and range guarantees

The implementation stays in the Nova product layer; the language-independent `Symbol`, `SymbolSnapshot`, and semantic-token encoder remain unchanged. Reference tokens are derived from the exact current `SemanticSnapshot`, while the existing semantic-token request path retains its workspace snapshot publication gate, cancellation checkpoints, and same-version replacement rejection.

Full and range requests share the same token construction. Range requests include only declarations and references intersecting the requested half-open span. Existing full/delta support therefore consumes the same deterministic reference-aware token stream.
