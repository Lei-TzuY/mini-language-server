# Versioned rename WorkspaceEdits

Rename responses now negotiate the strongest WorkspaceEdit representation supported by the client without changing Nova rename semantics.

When the client advertises `workspace.workspaceEdit.documentChanges = true`, same-file and cross-file rename responses use `WorkspaceEdit.documentChanges`. Editor-owned documents carry the exact integer version captured by the semantic/workspace snapshot that produced the edits. Detached closed workspace files use `version: null` as required by `OptionalVersionedTextDocumentIdentifier`; the server never invents a document version for disk-only sources.

When the client does not advertise `documentChanges`, rename keeps the existing `WorkspaceEdit.changes` representation for compatibility. The edit ranges and ordering are otherwise identical.

The language-independent server owns the WorkspaceEdit renderer. Base rename, workspace-aware rename, and the final collision-safe Nova rename all call the same helper, so capability negotiation and version ownership cannot diverge between implementations.

Cross-file edits remain deterministic by URI, and edits within each file remain deterministic by source offset. Same-name rename produces an empty `documentChanges` list for negotiated clients and an empty `changes` map for legacy clients.

Versioned payloads do not replace stale-result protection. The exact semantic/workspace snapshot set is still revalidated at the publication boundary. Closed-file inputs are additionally reread before source-mutating rename publication; disk drift refreshes the detached index and returns `Content modified` instead of publishing stale ranges with a null version.
