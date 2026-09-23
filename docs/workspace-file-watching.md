# Dynamic Nova workspace file watching

The server can dynamically register the standard LSP `workspace/didChangeWatchedFiles` notification so editor-observed filesystem changes advance the bounded detached Nova workspace index.

## Registration lifecycle

The capability is enabled only when the client advertises:

```text
workspace.didChangeWatchedFiles.dynamicRegistration = true
```

After the `initialized` notification, the server queues one tracked `client/registerCapability` request for:

- method: `workspace/didChangeWatchedFiles`
- glob: `**/*.nova`
- kind: `7` (create + change + delete)

The registration ID is stable for the session and registration is attempted at most once. Notifications are ignored until the exact registration request succeeds with a null result. That success boundary immediately performs one bounded closed-index rescan so filesystem changes that occurred between the initial workspace scan and watcher installation cannot be permanently missed. Registration error, cancellation, shutdown retirement, or a client without dynamic-registration support leaves watched-file handling inactive and does not perform the registration-window rescan. Duplicate `initialized` notifications never create a second registration.

## Workspace transition

A watched-file notification is accepted only as a complete batch. Every entry must contain a non-empty URI and one standard file-change type: create (1), change (2), or delete (3). A malformed entry rejects the complete batch.

The notification triggers the existing bounded closed-file rescan only when at least one entry identifies an in-scope local `.nova` path. The rescan therefore reuses all existing filesystem limits, UTF-8 decoding, symlink exclusions, RFC-safe URI identity, open-buffer precedence, and exact `WorkspaceSymbolIndex` generation semantics.

The same path handles:

- content edits to an already indexed closed file;
- newly created Nova files even when resource-operation create notifications are unavailable;
- deleted Nova files even when resource-operation delete notifications are unavailable.

Open editor buffers remain authoritative. A disk watcher event for an open URI cannot replace the live document semantic snapshot.

## Concurrency and refresh

`workspace/didChangeWatchedFiles` is a live snapshot mutation in the stdio runtime. It can advance the exact workspace while a client request is computing, causing existing compare-and-commit guards to reject stale results.

When a watcher transition changes the workspace and the client negotiated workspace diagnostic refresh, the existing coalesced `workspace/diagnostic/refresh` request path is reused. Closed-file pull diagnostics then observe the new exact detached snapshot.

This feature relies on client-provided LSP file watching. The server does not create a native operating-system watcher and does not claim remote-provider support.
