# Language Tooling Core Stability Checkpoint

This document defines the first stable maintenance boundary for `mini-language-server`.

## Stable tooling chain

The checkpoint covers:

1. bounded JSON-RPC/LSP framing with per-line, total-header-byte, header-count, and payload limits, plus an executable cancellation-aware stdio host with one active client-request worker, foreground document/workspace/configuration mutation, guarded live responses to delivered server requests, and coalesced outbound wakeups for server requests created by the active worker itself; consumer request ownership and trace publication become atomic before transport visibility, terminal stdin EOF/framing/read failure and stdout write/flush failure abort active transport-owned request generations, retire outbound ownership, quiesce later inbound dispatch after output loss, and detach the wakeup before non-zero exit; client requests remain serial, first-shutdown interruption is a bounded terminal exception to ordinary lifecycle FIFO while `exit` and repeated lifecycle traffic remain deferred, and lifecycle/outbox ordering, notification-only message-shape ownership, terminal exit idempotence, and standard redacted `$/setTrace` / `$/logTrace` observability remain preserved
2. document snapshots, versions, and incremental edits
3. source positions and spans with session-wide LSP UTF-8/UTF-16/UTF-32 position encoding negotiation
4. syntax snapshot publication
5. symbol snapshot publication
6. semantic reference publication
7. diagnostic publication plus push/pull rendering with negotiated version/tag/related-location metadata, exact direct related-document pull dependencies, and commit-gated related-document partial streaming
8. definition/reference/rename queries
9. request cancellation, including shutdown/exit retirement of all active client-to-server request generations
10. tracked server-to-client JSON-RPC requests with lock-backed pending/outbox ownership, atomic consumer `on_queued` publication, coalesced empty-to-non-empty transport wakeups, live response delivery after proven send, cancellation/retirement of stale generation-bound requests, shutdown/exit retirement that prevents post-lifecycle response delivery, dynamic capability registration, coalesced negotiated workspace refresh lifecycles, standard `$/progress` partial-result and negotiated work-done lifecycles including exact-commit workspace-symbol streaming, response retirement, and validated consumer response delivery
11. terminal outbound-notification quiescence: shutdown preserves only lifecycle-required cancellation notifications for already-delivered server requests, exit preserves no notification traffic, and diagnostics/progress/trace publication shares one lock-backed terminal append gate
11. workspace-folder scope generations for cross-file tooling, including bounded detached local closed-file Nova indexing with open-buffer precedence, pull-only workspace diagnostics, unified open+detached rename preflight, dynamically registered client-mediated `.nova` filesystem watching, and disk revalidation before source-mutating rename publication
12. stale-result suppression across concurrent document, semantic, workspace-scope, and workspace-dependent semantic-token delta publication

The core invariant is generational identity: a derived result is valid only while the exact parent snapshot that produced it remains current. Structural equality or a matching numeric document version is not enough. Workspace-wide derived results that capture a complete semantic or scoped open-document workspace must also reject publication when a relevant URI is added, removed, or leaves/re-enters workspace-folder scope after capture, even if every previously captured snapshot object remains current. Scoped queries may ignore mutations to documents that were outside the captured scope.

## Responsibility boundaries

| Layer | Owns | Must not silently own |
| --- | --- | --- |
| protocol/server | bounded JSON-RPC/LSP framing, cancellation-aware single-request-worker stdio session hosting with foreground document/workspace-scope/formatting-configuration mutation, live-shutdown retirement plus lifecycle and message-shape ownership, bidirectional request routing, lock-backed server-request tracking/response delivery/cancellation, terminal request/notification quiescence, transport-abort ownership retirement, and exit idempotence, redacted trace observability, session position-encoding negotiation, workspace-folder lifecycle, negotiated diagnostic metadata, LSP result rendering | language parsing/type rules |
| document store | current text snapshot, version/generation transitions | syntax or semantic interpretation |
| syntax store | current parsed result for one exact document | symbol resolution |
| symbol index | deterministic symbols for one exact syntax snapshot | reference semantics |
| semantic database | resolved references bound to exact symbol objects | protocol rendering |
| diagnostic store | diagnostics bound to the primary semantic snapshot plus any exact cross-file related semantic parents | document mutation |
| request tracker | active cancellation, one-shot transport-staged cancellation for already-read pending generations, terminal lifecycle retirement, and stale-document checkpoints | semantic freshness publication |

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
- remaining filesystem lifecycle surfaces: permission-aware filesystem transactions, OS-native or remote watcher providers, full closed-file diagnostic/repair parity (push/remaining advanced data-flow diagnostics plus quick fixes beyond the bounded condition-type and scalar constant-condition/division-zero/conversion-range analysis, unresolved-function, call-site argument-count/type, return/local/assignment type-mismatch, immutable-assignment, missing-return, unreachable-code, and typed-var definite-initialization repairs), and module/import rewrite semantics remain future executable phases; bounded unopened local-file indexing, exact open+detached `willCreateFiles` / `willDeleteFiles` / `willRenameFiles` preflight, negotiated create/delete/rename reconciliation, dynamically registered client-mediated watched-file reconciliation, and pull-only workspace diagnostics are now part of the stable workspace substrate
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
