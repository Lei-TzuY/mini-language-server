# mini-language-server

A language-independent language-server and compiler-tooling laboratory focused on versioning, stale-result suppression, cancellation, and deterministic semantic publication.

The repository has moved well beyond the original JSON-RPC bootstrap. Its first stable tooling-core checkpoint is:

```text
LSP / JSON-RPC lifecycle
        -> document snapshots + incremental edits
        -> syntax snapshots
        -> symbol index
        -> semantic references
        -> diagnostics + navigation/rename
        -> cancellation + stale-result suppression
```

## Current capabilities

- bounded LSP `Content-Length` framing and JSON-RPC lifecycle handling
- document open/change/close with monotonic versions and incremental edits
- source positions/spans with LSP coordinate conversion
- version-bound syntax, symbol, semantic, and diagnostic snapshots
- compare-and-commit publication guards across the derived-cache chain
- `textDocument/definition`
- `textDocument/references`
- safe single-document `textDocument/rename`
- push diagnostics with stale-notification suppression
- request cancellation and stale-document rejection
- deterministic concurrency regressions for same-version snapshot replacement and out-of-order publication
- CI across Ubuntu, macOS, and Windows on Python 3.11 and 3.13

## Checkpoint scope

The generic tooling substrate is the stable unit. It deliberately does **not** claim to be a full production LSP implementation or a finished Nova language server.

A real Nova adapter, hover, completion, semantic tokens, multi-file/workspace indexing, richer rename semantics, and additional protocol capabilities are explicit future phases. They should not be added merely to manufacture activity after the core checkpoint is stable.

See [`docs/stability-checkpoint.md`](docs/stability-checkpoint.md) for the maintenance boundary.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
```

Every derived result must remain bound to the exact document/syntax/symbol/semantic generation that produced it. Late work must be rejected rather than overwrite a newer snapshot.
