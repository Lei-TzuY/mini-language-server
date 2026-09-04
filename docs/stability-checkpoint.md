# Language Tooling Core Stability Checkpoint

This document defines the first stable maintenance boundary for `mini-language-server`.

## Stable tooling chain

The checkpoint covers:

1. JSON-RPC/LSP framing and lifecycle
2. document snapshots, versions, and incremental edits
3. source positions and spans
4. syntax snapshot publication
5. symbol snapshot publication
6. semantic reference publication
7. diagnostic publication and notification emission
8. definition/reference/rename queries
9. request cancellation
10. stale-result suppression across concurrent document and semantic replacement

The core invariant is generational identity: a derived result is valid only while the exact parent snapshot that produced it remains current. Structural equality or a matching numeric document version is not enough.

## Responsibility boundaries

| Layer | Owns | Must not silently own |
| --- | --- | --- |
| protocol/server | JSON-RPC lifecycle, request routing, LSP result rendering | language parsing/type rules |
| document store | current text snapshot, version/generation transitions | syntax or semantic interpretation |
| syntax store | current parsed result for one exact document | symbol resolution |
| symbol index | deterministic symbols for one exact syntax snapshot | reference semantics |
| semantic database | resolved references bound to exact symbol objects | protocol rendering |
| diagnostic store | diagnostics bound to one exact semantic snapshot | document mutation |
| request tracker | cancellation and stale-document checkpoints | semantic freshness publication |

## Maintenance triggers

After this checkpoint, changes should normally be driven by:

- CI or protocol regression
- a reproducible stale-result/cancellation/concurrency bug
- incorrect LSP framing/lifecycle/document-version behavior
- a real language adapter exposing a generic substrate defect
- an explicitly selected, bounded next-phase capability

Do not create commits solely because a scheduled run occurred.

## Future phases, not maintenance filler

The following remain deliberate future work:

- a real Nova adapter backed by Nova syntax/semantic data
- hover and completion
- semantic tokens
- multi-file/workspace symbol and reference indexing
- cross-file rename and workspace edits
- broader LSP compliance surface
- editor-specific integration layers

These should begin only with explicit acceptance criteria and end-to-end adapter/protocol tests.

## Validation gate

Before integrating maintenance changes:

- run Ruff
- run the complete pytest suite
- keep Ubuntu/macOS/Windows and Python 3.11/3.13 CI green
- add focused regression coverage for concurrency/protocol changes
- verify the exact candidate head is green and the base has not drifted
