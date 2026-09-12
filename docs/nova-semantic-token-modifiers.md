# Nova reference semantic tokens and binding modifiers

The final Nova product extends semantic tokens beyond declaration-only symbol snapshots. Exact semantic references receive the token type of their resolved target, so function calls, parameter uses, and local-variable uses are highlighted from the same `SemanticSnapshot` that powers definition and references.

## Negotiated modifiers

When the client advertises semantic-token modifier support, the server exposes the supported intersection in deterministic legend order:

- `declaration` marks Nova function, parameter, and local declarations.
- `definition` marks executable Nova function definitions. Parameters and locals remain declarations only.
- `readonly` marks `let` local declarations and every semantic reference resolved to that exact immutable local.
- `modification` marks resolved semantic references that are assignment targets.
- `defaultLibrary` marks only the implemented Nova numeric intrinsic surface: the `UInt`/`Int` type tokens and `MIN`, `MAX`, `from`, and `from_uint` member tokens recognized by the existing intrinsic semantic-token layer.
- `static` marks the implemented associated numeric intrinsic members (`MIN`, `MAX`, `from`, and `from_uint`) while leaving the owning `UInt`/`Int` type token unmodified.

`var` locals remain mutable and therefore do not receive `readonly`. Parameters are not labeled readonly because the current Nova mutability contract only rejects assignment to `let` locals. An attempted assignment to a `let` local is still a write in editor semantics, so its target token carries both `readonly` and `modification` even when diagnostics reject the assignment. Unsupported modifiers are not advertised and never set in the bitset.

Function definition classification reuses the exact Nova symbol snapshot: only function symbols receive `definition`, while parameter and local declaration symbols do not. Assignment classification uses the Nova trivia-masked code view and only applies to references already resolved by the exact semantic snapshot. Identifiers inside comments or quoted strings therefore cannot become modification tokens, and equality operators are not mistaken for assignment. Default-library/static classification uses the same trivia-aware intrinsic recognition that creates the numeric intrinsic tokens, so lookalikes in comments and strings are not marked.

Clients that support semantic tokens but no modifiers still receive reference and intrinsic tokens with a zero modifier bitset.

## Snapshot and range guarantees

The implementation stays in the Nova product layer; the language-independent `Symbol`, `SymbolSnapshot`, and semantic-token encoder remain unchanged. Reference and intrinsic modifier tokens are derived from the exact current `SemanticSnapshot`, while the existing semantic-token request path retains its workspace snapshot publication gate, cancellation checkpoints, and same-version replacement rejection.

Full and range requests share the same token construction. Range requests include only declarations, references, and intrinsic tokens intersecting the requested half-open span. Existing full/delta support therefore consumes the same deterministic modifier-aware token stream.
