# Workspace symbol partial results and work-done progress

The Nova workspace symbol provider supports the standard LSP progress tokens on `workspace/symbol` without weakening the exact workspace publication boundary.

## Partial results

When `partialResultToken` is absent, behavior is unchanged: the final JSON-RPC response contains the complete deterministic symbol array.

When a valid string or integer `partialResultToken` is present, the server builds the same deterministic symbol sequence and streams it as `$/progress` array chunks of up to 16 symbols. The final response is an empty array because the complete symbol payload has already been delivered through progress.

Partial symbol data is queued only inside `WorkspaceSymbolIndex.commit_snapshots_if_current`. If any indexed semantic snapshot is added, removed, or replaced after capture, the exact commit fails with `Content modified` and no partial symbol chunk is emitted. This avoids exposing a prefix assembled from a stale workspace generation.

## Work-done progress

A valid `workDoneToken` is always accepted syntactically. Progress notifications are emitted only when the client negotiated `window.workDoneProgress`.

A supported request emits:

- `begin` with title `Workspace symbols` and cancellable status,
- bounded `report` updates while deterministic symbol rendering advances,
- `end` with a completion, cancellation, or workspace-changed message.

Work-done progress carries operation status only. It is independent of partial-result delivery, so clients may request either token or both.

Cancellation returns LSP `Request cancelled` and closes an already-started work-done lifecycle. Workspace drift returns `Content modified`. Neither failure path publishes partial symbol data before the exact workspace commit succeeds.

Both token fields reject booleans, containers, and other non-string/non-integer values as invalid request parameters.
