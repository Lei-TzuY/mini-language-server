# Workspace file rename transactions

The Nova workspace layer supports one bounded LSP file-operation workflow: negotiated `workspace/didRenameFiles` for Nova documents that are already open in the current session.

## Contract

Clients opt in through `workspace.fileOperations.didRename`. The server advertises one `workspace.fileOperations.didRename` registration filter for `file:**/*.nova` operations.

A rename notification is treated as a live snapshot mutation. While an ordinary request is running, the runtime may dispatch the notification on the foreground mutation lane so requests captured from the old document/workspace identity fail their existing exact-snapshot guards instead of publishing against a renamed file.

For every open Nova source in one notification batch, `DocumentStore.rename_many()` preflights the full set before mutation. Source URIs and destination URIs must be unique, destination URIs may not collide with an unrelated open document, and URI swaps between documents participating in the same batch are supported. A rejected batch leaves every open document unchanged.

Successful rekeying preserves language id, text, and document version but creates new immutable `Document` identities under the destination URIs. Old URI keys are removed from syntax, symbol, semantic, diagnostic, workspace-symbol, and semantic-token-delta state. The Nova adapter then rebuilds the exact derived snapshot chain for each destination URI from the preserved content.

Workspace-folder scope is evaluated again at the destination. A file renamed outside the captured workspace remains an open local document with local Nova semantics, but it no longer contributes declarations/references to cross-file workspace tooling. Cross-file diagnostics are reconciled once after the complete batch, and workspace-wide refresh consumers observe one new workspace generation.

The server explicitly clears push diagnostics for the old URI. New diagnostics and navigation locations use only the destination URI. Semantic-token delta history is not migrated: result ids from the old URI cannot be reused as a delta lineage for the renamed document.

## Deliberate boundary

This milestone does not implement `workspace/willRenameFiles`, file creation/deletion operations, unopened-file indexing, filesystem I/O, or speculative source edits for module/import paths. Those require a concrete Nova module/file semantic model rather than empty protocol handlers.
