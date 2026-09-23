# Workspace file rename transactions

The Nova workspace layer supports a bounded LSP file-operation workflow for already-open Nova documents and uses the same `workspace/didRenameFiles` notification as a rescan trigger for the local closed-file workspace index.

## Contract

Clients negotiate `workspace.fileOperations.willRename` and `didRename` independently. The server advertises matching `file:**/*.nova` filters only for the operations the client supports. `workspace/willRenameFiles` is a pure preflight request: it validates the exact current set of affected open Nova source/destination identities, rejects duplicate destinations or unrelated open-document collisions with `RequestFailed`, honors cancellation, and returns `Content modified` if any affected document identity changes before publication. A valid preflight returns `null` because Nova currently has no import/module path edits to apply. `workspace/didRenameFiles` remains the commit notification.

A rename notification is treated as a live snapshot mutation. While an ordinary request is running, the runtime may dispatch the notification on the foreground mutation lane so requests captured from the old document/workspace identity fail their existing exact-snapshot guards instead of publishing against a renamed file.

For every open Nova source in one notification batch, `DocumentStore.rename_many()` preflights the full set before mutation. Source URIs and destination URIs must be unique, destination URIs may not collide with an unrelated open document, and URI swaps between documents participating in the same batch are supported. A rejected batch leaves every open document unchanged.

Successful rekeying preserves language id, text, and document version but creates new immutable `Document` identities under the destination URIs. Old URI keys are removed from syntax, symbol, semantic, diagnostic, workspace-symbol, and semantic-token-delta state. The Nova adapter then rebuilds the exact derived snapshot chain for each destination URI from the preserved content.

Workspace-folder scope is evaluated again at the destination. A file renamed outside the captured workspace remains an open local document with local Nova semantics, but it no longer contributes declarations/references to cross-file workspace tooling. Cross-file diagnostics are reconciled once after the complete batch, and workspace-wide refresh consumers observe one new workspace generation.

The server explicitly clears push diagnostics for the old URI. New diagnostics and navigation locations use only the destination URI. Semantic-token delta history is not migrated: result ids from the old URI cannot be reused as a delta lineage for the renamed document.

## Deliberate boundary

`willRenameFiles` still preflights only server-owned open-document identity; it does not claim filesystem permission checks or closed-file resource-rename validation. After the client commits a rename, `didRenameFiles` reconciles the bounded local closed-file index so disk-only Nova declarations move to their current URI. File creation/deletion notifications, filesystem watchers, remote providers, and module/import path rewrites remain separate milestones.
