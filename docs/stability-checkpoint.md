# Language Tooling Core Stability Checkpoint

This document defines the first stable maintenance boundary for `mini-language-server`.

## Stable tooling chain

The checkpoint covers:

1. bounded JSON-RPC/LSP framing with per-line, total-header-byte, header-count, and payload limits, plus an executable synchronous stdio host that connects framing to the final product and preserves lifecycle/outbox ordering; lifecycle includes notification-only message-shape ownership, terminal exit idempotence, and standard redacted `$/setTrace` / `$/logTrace` observability
2. document snapshots, versions, and incremental edits
3. source positions and spans with session-wide LSP UTF-8/UTF-16/UTF-32 position encoding negotiation
4. syntax snapshot publication
5. symbol snapshot publication
6. semantic reference publication
7. diagnostic publication plus push/pull rendering with negotiated version/tag/related-location metadata, exact direct related-document pull dependencies, and commit-gated related-document partial streaming
8. definition/reference/rename queries
9. request cancellation, including shutdown/exit retirement of all active client-to-server request generations
10. tracked server-to-client JSON-RPC requests, cancellation/retirement of proven-stale generation-bound requests, shutdown/exit retirement that prevents post-lifecycle response delivery, dynamic capability registration, coalesced negotiated workspace refresh lifecycles, standard `$/progress` partial-result and negotiated work-done lifecycles including exact-commit workspace-symbol streaming, response retirement, and validated consumer response delivery
11. terminal outbound-notification quiescence: shutdown preserves only lifecycle-required cancellation notifications for already-delivered server requests, exit preserves no notification traffic, and diagnostics/progress/trace publication shares one lock-backed terminal append gate
11. workspace-folder scope generations for cross-file tooling
12. stale-result suppression across concurrent document, semantic, workspace-scope, and workspace-dependent semantic-token delta publication

The core invariant is generational identity: a derived result is valid only while the exact parent snapshot that produced it remains current. Structural equality or a matching numeric document version is not enough. Workspace-wide derived results that capture a complete semantic or scoped open-document workspace must also reject publication when a relevant URI is added, removed, or leaves/re-enters workspace-folder scope after capture, even if every previously captured snapshot object remains current. Scoped queries may ignore mutations to documents that were outside the captured scope.

## Responsibility boundaries

| Layer | Owns | Must not silently own |
| --- | --- | --- |
| protocol/server | bounded JSON-RPC/LSP framing, executable synchronous stdio session hosting, lifecycle and message-shape ownership, bidirectional request routing, server-request tracking/response delivery/cancellation, terminal request/notification quiescence and exit idempotence, redacted trace observability, session position-encoding negotiation, workspace-folder lifecycle, negotiated diagnostic metadata, LSP result rendering | language parsing/type rules |
| document store | current text snapshot, version/generation transitions | syntax or semantic interpretation |
| syntax store | current parsed result for one exact document | symbol resolution |
| symbol index | deterministic symbols for one exact syntax snapshot | reference semantics |
| semantic database | resolved references bound to exact symbol objects | protocol rendering |
| diagnostic store | diagnostics bound to the primary semantic snapshot plus any exact cross-file related semantic parents | document mutation |
| request tracker | cancellation, terminal lifecycle retirement, and stale-document checkpoints | semantic freshness publication |

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
- WorkspaceEdit resource operations when a concrete editor workflow requires file create/rename/delete semantics
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
