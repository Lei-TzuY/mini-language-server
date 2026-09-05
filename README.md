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
- compare-and-commit publication guards across the derived-cache chain
- definition, references, prepareRename, and rename, including uniquely resolved cross-file Nova functions
- hover, completion, semantic tokens, workspace symbol search, and negotiated signature help
- executable Nova adapter semantics for functions, typed parameters, locals, scoped references, and deterministic unresolved/duplicate diagnostics
- exact-workspace Nova call diagnostics, including unresolved/ambiguous functions and argument-count mismatches against the current unique declaration
- push diagnostics with stale-notification suppression
- request cancellation and stale-document rejection
- deterministic concurrency regressions for same-version snapshot replacement, close/reopen, and out-of-order publication
- CI across Ubuntu, macOS, and Windows on Python 3.11 and 3.13

## Checkpoint scope

The generic tooling substrate remains language-independent. Nova-specific parsing, name-resolution rules, typed function metadata, cross-file product behavior, and diagnostics stay in the Nova adapter/product composition instead of leaking into the generic stores and query layer.

The project is still intentionally bounded rather than a complete production LSP or full Nova compiler front end. New slices should add executable semantics or protocol behavior with exact-snapshot regressions, not empty handlers, adapters, or scaffolding.

See [`docs/stability-checkpoint.md`](docs/stability-checkpoint.md) for the maintenance boundary.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
```

Every derived result must remain bound to the exact document/syntax/symbol/semantic generation that produced it. Late work must be rejected rather than overwrite a newer snapshot.
