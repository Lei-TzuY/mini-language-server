# mini-language-server

A language-independent language-server and compiler-tooling laboratory focused on versioning, stale-result suppression, cancellation, and deterministic semantic publication.

The repository has moved well beyond the original JSON-RPC bootstrap. Its stable tooling chain is:

```text
LSP / JSON-RPC lifecycle
        -> document snapshots + incremental edits
        -> syntax snapshots
        -> symbol index
        -> semantic references
        -> diagnostics + navigation/rename
        -> workspace indexing + cross-file Nova tooling
        -> cancellation + stale-result suppression
```

## Current capabilities

- bounded LSP `Content-Length` framing and JSON-RPC lifecycle handling
- document open/change/close with monotonic versions and incremental edits
- source positions/spans with LSP coordinate conversion
- version-bound syntax, symbol, semantic, diagnostic, and workspace snapshots
- compare-and-commit publication guards across the derived-cache chain, including exact open-document and diagnostic snapshot-set guards for workspace reads
- definition, references, document highlights, linked editing ranges, prepareRename, and rename, including uniquely resolved cross-file Nova functions; the final Nova product rejects workspace function-name collisions before publishing rename edits
- hover, lexical-scope-aware completion, semantic tokens (full, range, and negotiated full/delta), document symbols, workspace symbol search, negotiated signature help, negotiated Nova parameter inlay hints, exact-snapshot Nova function folding ranges, negotiated Nova lexical selection ranges, negotiated Nova whole-document formatting, negotiated Nova range formatting, negotiated Nova on-type indentation, negotiated exact-workspace Nova call hierarchy, and negotiated exact-workspace Nova reference-count CodeLens
- negotiated `textDocument/diagnostic` and `workspace/diagnostic` pull reports with deterministic result IDs, unchanged-report support, cancellation, and exact diagnostic-snapshot publication guards
- executable Nova adapter semantics for functions, typed parameters, explicitly typed locals, locals, scoped references, return statements, and deterministic unresolved/duplicate diagnostics; the final product lexically masks line comments, block comments, and quoted strings before structural scans while preserving exact source offsets
- exact-workspace Nova hover type information for explicitly typed parameters, explicitly typed locals, literal-initialized locals, exact-reference local aliases, bounded uniquely resolved direct function-call initializers, bounded integer-arithmetic-initialized locals, bounded comparison-initialized locals, and bounded logical-expression-initialized locals when type knowledge is available; expression-derived local knowledge also feeds lexical completion details and local type inlay hints, while mixed/unknown expressions remain conservative
- conservative Nova function-result inference for uniquely resolved unannotated functions whose value-bearing returns consistently resolve to one bounded Int, String, or Bool type through literals, exact semantic parameter/local references with bounded explicit type knowledge, local alias chains rooted in bounded direct function-call initializers, acyclic chains of uniquely resolved direct calls, or bounded integer arithmetic/comparison/logical expressions whose operands resolve through the same exact semantic/workspace snapshot; ambiguous targets, cycles, conflicting/unsupported/unknown returns remain unknown, and explicit return annotations are never overridden
- exact-workspace Nova completion details for bounded parameter/local types and uniquely resolved same-file or cross-file function signatures; ambiguous function names remain conservative
- exact-workspace Nova call diagnostics, including unresolved/ambiguous functions, argument-count mismatches, and bounded argument-type checks for Int, String, and Bool literals plus exact semantic references to explicitly typed parameters, explicitly typed locals, literal-initialized locals, and local aliases whose initializer resolves through the same exact semantic snapshot
- bounded Nova integer arithmetic expression typing propagates Int through `+`, `-`, `*`, `/`, and `%` when every operand resolves to Int from the exact semantic/workspace snapshot; mixed or unknown operands remain conservative, and the result participates in argument/return diagnostics and their existing quick fixes
- bounded Nova comparison expression typing produces Bool for `==`/`!=` when both operands resolve to the same bounded Int/String/Bool type, and for `<`/`<=`/`>`/`>=` when both operands resolve to Int; operands reuse exact-snapshot arithmetic/reference/function-call typing, while mixed, unknown, or chained comparisons remain conservative
- bounded Nova logical expression typing produces Bool for Bool-only `&&`/`||` operands and unary `!`, composing the existing exact-snapshot comparison/arithmetic/reference/function-call inference path; mixed, malformed, unknown, ambiguous, or cyclic inputs remain conservative, and the result participates in argument/return validation, function-result inference, and local tooling
- bounded Nova return validation reports deterministic `nova.return-type` diagnostics when Int, String, or Bool literals, exact semantic references with bounded parameter/local type knowledge, or uniquely resolved same-file/cross-file function calls with bounded explicit or inferred result types conflict with an explicit function return annotation, and `nova.missing-return` when an explicit Int/String/Bool return function has no top-level value-bearing return; nested `if`/`while` returns are still type-checked but conservatively do not prove every path returns
- bounded explicit-local validation reports deterministic `nova.local-type` diagnostics when an Int, String, or Bool annotation conflicts with a bounded literal/reference/function-call, integer arithmetic, comparison, or logical expression whose type is known from the exact current workspace snapshot; unknown or unsupported compound expressions remain conservative
- exact-workspace Nova call hierarchy resolves uniquely named functions and deterministically aggregates incoming callers and outgoing callees without guessing across ambiguous declarations
- exact-workspace Nova reference CodeLens counts uniquely resolved cross-file call sites and exposes an exact-workspace reference-location command while suppressing ambiguous or stale results
- literal-aware Nova call parsing so quoted commas, parentheses, and escaped quotes do not corrupt argument boundaries
- negotiated Nova quick fixes for unresolved functions, unresolved local names, argument-count mismatches, argument-type mismatches, return-type mismatches, missing value returns, explicit-local initializer type mismatches, and duplicate function/parameter/local declarations, all gated on exact current diagnostic and workspace snapshots
- duplicate-declaration quick fixes deterministically choose collision-free Nova identifiers within the relevant function or declaration namespace and edit only the exact diagnosed declaration span
- trivia-aware Nova formatting reindents structural braces while preserving non-leading source text; braces inside comments and quoted strings do not affect indentation, and responses are gated on the exact current semantic/document snapshot
- conservative Nova range formatting reindents only leading whitespace spans fully contained in the requested range, computes structural depth from the complete trivia-aware document view, and never publishes edits that escape the requested range
- conservative on-type Nova formatting reindents only the current line when `}` is typed, using the same trivia-aware structural view and exact semantic/document publication guard
- push diagnostics with stale-notification suppression
- request cancellation and stale-document rejection
- deterministic concurrency regressions for same-version snapshot replacement, close/reopen, and out-of-order publication
- CI across Ubuntu, macOS, and Windows on Python 3.11 and 3.13

## Checkpoint scope

The generic tooling substrate remains language-independent. Nova-specific parsing, lexical trivia masking, name-resolution rules, typed function metadata, conservative cycle-safe bounded function-result inference from literals, exact semantic explicit typed references, local direct-call/alias chains, uniquely resolved direct-call chains, exact-snapshot bounded arithmetic/comparison/logical expressions, arithmetic/comparison/logical-derived local type surfacing, bounded integer arithmetic, comparison, and logical expression typing, explicit local-type annotations and bounded initializer validation including exact-workspace function/reference/arithmetic/comparison/logical result propagation, bounded return-type validation including exact-workspace function-call result propagation and conservative top-level return-path proof, trivia-aware exact-workspace function-call initializer inference, cross-file product behavior, rename-collision policy, duplicate-declaration repair policy, call-hierarchy ownership/routing, reference-CodeLens counting/command policy, diagnostics, quick-fix policy, parameter inlay hints, function-body folding rules, lexical selection-range ownership, document-formatting, range-formatting, and on-type indentation policy, literal-aware call parsing, bounded typed parameter/local propagation, and lexical completion visibility stay in the Nova adapter/product composition instead of leaking into the generic stores and query layer. Pull-diagnostic protocol composition is language-independent and reads exact DiagnosticSnapshots without changing Nova diagnostic policy; workspace pulls additionally pin the exact open-document and diagnostic snapshot sets through generic compare-and-commit guards. Semantic-token delta composition is language-independent and wraps the exact full-token publication boundary without adding Nova-specific token rules.

The project is still intentionally bounded rather than a complete production LSP or full Nova compiler front end. New slices should add executable semantics or protocol behavior with exact-snapshot regressions, not empty handlers, adapters, or scaffolding.

See [`docs/stability-checkpoint.md`](docs/stability-checkpoint.md) for the maintenance boundary.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
```

Every derived result must remain bound to the exact document/syntax/symbol/semantic generation that produced it. Late work must be rejected rather than overwrite a newer snapshot.
