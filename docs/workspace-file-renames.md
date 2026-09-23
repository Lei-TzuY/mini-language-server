# Workspace file rename transactions

The Nova workspace layer supports a bounded LSP file-operation workflow for already-open Nova documents and uses the same `workspace/didRenameFiles` notification as a rescan trigger for the local closed-file workspace index.

## Contract

Clients negotiate `workspace.fileOperations.willRename` and `didRename` independently. The server advertises matching `file:**/*.nova` filters only for the operations the client supports. `workspace/willRenameFiles` is a pure preflight request over the complete server-known Nova workspace: it captures canonical affected URI identities, open document snapshots, the combined open/detached workspace snapshot set, workspace-folder generation, and affected detached mappings. It rejects duplicate canonical destinations, unrelated open-document collisions, and destinations already occupied by detached indexed Nova files with `RequestFailed`; closed/open swaps inside the same moving batch remain valid. Detached inputs are reread before publication, and document/workspace/folder or disk drift returns `Content modified`. A valid preflight returns `null` because Nova currently has no import/module path edits to apply. `workspace/didRenameFiles` remains the commit notification.

A rename notification is treated as a live snapshot mutation. While an ordinary request is running, the runtime may dispatch the notification on the foreground mutation lane so requests captured from the old document/workspace identity fail their existing exact-snapshot guards instead of publishing against a renamed file.

For every open Nova source in one notification batch, `DocumentStore.rename_many()` preflights the full set before mutation. Source URIs and destination URIs must be unique, destination URIs may not collide with an unrelated open document, and URI swaps between documents participating in the same batch are supported. A rejected batch leaves every open document unchanged.

Successful rekeying preserves language id, text, and document version but creates new immutable `Document` identities under the destination URIs. Old URI keys are removed from syntax, symbol, semantic, diagnostic, workspace-symbol, and semantic-token-delta state. The Nova adapter then rebuilds the exact derived snapshot chain for each destination URI from the preserved content.

Workspace-folder scope is evaluated again at the destination. A file renamed outside the captured workspace remains an open local document with local Nova semantics, but it no longer contributes declarations/references to cross-file workspace tooling. Cross-file diagnostics are reconciled once after the complete batch, and workspace-wide refresh consumers observe one new workspace generation.

The server explicitly clears push diagnostics for the old URI. New diagnostics and navigation locations use only the destination URI. Semantic-token delta history is not migrated: result ids from the old URI cannot be reused as a delta lineage for the renamed document.

## Deliberate boundary

`willRenameFiles` now preflights both editor-owned open identities and bounded detached Nova identities already known to the server, but it deliberately does not claim filesystem permission/access checks, unindexed external-resource discovery, remote-provider authority, or module/import path rewrites. After the client commits a rename, `didRenameFiles` still owns mutation/reconciliation and rescans the bounded closed-file index. Negotiated `workspace/didCreateFiles` and `workspace/didDeleteFiles` continue to use the same closed-index reconciliation boundary without mutating open-document ownership. Filesystem watchers, remote providers, permission-aware filesystem transactions, and module/import path rewrites remain separate milestones.
