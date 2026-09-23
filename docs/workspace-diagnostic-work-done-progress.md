# Workspace diagnostic work-done progress

The language-independent pull-diagnostic product supports LSP `workDoneToken` on `workspace/diagnostic` when the client advertises `window.workDoneProgress = true`.

Work-done progress and partial-result progress have distinct responsibilities:

- `workDoneToken` communicates operation status only through `begin`, `report`, and `end` values.
- `partialResultToken` carries diagnostic report data and remains guarded by the complete exact workspace commit boundary.

For a supported client and a valid work-done token, the server emits:

1. `begin` with title `Workspace diagnostics`, cancellable true, and percentage 0;
2. one deterministic `report` after each captured workspace document is processed, with integer percentage and an exact processed/total message;
3. `end` after the exact workspace commit succeeds.

If cancellation occurs after `begin`, the server emits an `end` value identifying cancellation before returning the standard Request cancelled error. If folder scope, complete in-scope document membership, diagnostic snapshots, or cross-file related semantic parents become stale after work begins, the server emits an `end` value identifying workspace change before returning Content modified.

No semantic diagnostic data is carried by work-done progress. In a request using both tokens, work-done begin/report notifications precede the exact-commit partial diagnostic chunks and the work-done end follows them. A stale request may therefore emit status progress but never emits stale partial diagnostic data.

The token must be a string or integer; booleans, null, arrays, and objects are invalid params. A valid token is ignored when the client did not negotiate work-done progress support, preserving normal workspace diagnostic behavior without unsolicited UI progress.
