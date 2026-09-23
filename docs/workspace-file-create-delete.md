# Workspace file create/delete notifications

The closed-file Nova workspace index supports negotiated LSP post-operation notifications for local Nova sources:

- `workspace/didCreateFiles`
- `workspace/didDeleteFiles`

These notifications report filesystem operations that the client has already committed. The server does not create or delete files itself.

## Capability contract

The two capabilities are negotiated independently through `workspace.fileOperations.didCreate` and `workspace.fileOperations.didDelete`. The server advertises the same bounded local Nova filter used by rename lifecycle support:

`file:**/*.nova`

An unnegotiated notification is ignored. Malformed notification batches fail closed without partially mutating the workspace index.

## Create semantics

After a negotiated create notification, the server considers only RFC-deduplicated local `file:` URIs ending in `.nova` that are inside the configured workspace scope.

The file must already exist and satisfy the closed-index bounds: ordinary file, not a symlink, at most 2 MiB, valid UTF-8, and within the global 2048 detached-file cap. If an editor buffer already owns an RFC-equivalent URI identity, that live buffer remains authoritative and no detached duplicate is created.

A successful create publishes one detached semantic snapshot into `WorkspaceSymbolIndex`, re-evaluates workspace diagnostics for open Nova documents, and advances the workspace generation exactly once for the batch.

## Delete semantics

A negotiated delete notification removes only the detached contribution matching the deleted RFC-safe URI identity. It never removes an editor-owned open buffer, even when the backing file has disappeared.

If that open buffer later closes, the ordinary `didClose` restoration path attempts to reread disk. A deleted file therefore contributes nothing after the buffer relinquishes ownership.

Delete does not require the path to still exist; the notification describes an operation that has already completed.

## Runtime concurrency

Both notifications are live snapshot mutations in the cancellation-aware stdio host. While an ordinary request worker is active, create/delete notifications may advance on the foreground mutation lane. Any request that captured the prior workspace generation must fail its existing exact workspace commit guard instead of publishing against the old project universe.

## Deliberate boundary

This phase does not implement `workspace/willCreateFiles`, `workspace/willDeleteFiles`, filesystem watchers, remote source providers, closed-file diagnostics, permission checks, or module/import rewrite edits. It observes completed local resource operations and updates the existing detached workspace index.
