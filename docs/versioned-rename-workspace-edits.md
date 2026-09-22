# Versioned rename WorkspaceEdits

Rename responses now negotiate the strongest WorkspaceEdit representation supported by the client without changing Nova rename semantics.

When the client advertises `workspace.workspaceEdit.documentChanges = true`, same-file and cross-file rename responses use `WorkspaceEdit.documentChanges`. Every `TextDocumentEdit` carries the exact URI and document version captured by the semantic/workspace snapshot that produced its edits. The server does not re-read the document store to fill in versions at response time.

When the client does not advertise `documentChanges`, rename keeps the existing `WorkspaceEdit.changes` representation for compatibility. The edit ranges and ordering are otherwise identical.

The language-independent server owns the WorkspaceEdit renderer. Base rename, workspace-aware rename, and the final collision-safe Nova rename all call the same helper, so capability negotiation and version ownership cannot diverge between implementations.

Cross-file edits remain deterministic by URI, and edits within each file remain deterministic by source offset. Same-name rename produces an empty `documentChanges` list for negotiated clients and an empty `changes` map for legacy clients.

Versioned payloads do not replace stale-result protection. The exact semantic/workspace snapshot set is still revalidated at the publication boundary; if any captured document or workspace generation has been superseded, the request returns `Content modified` instead of publishing edits with obsolete versions.
