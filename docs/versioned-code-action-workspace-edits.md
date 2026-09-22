# Versioned code-action WorkspaceEdits

Nova quick-fix producers continue to describe only the semantic repair they want to make. They emit their existing document-scoped `WorkspaceEdit.changes` shape internally; the shared code-action assembly boundary is responsible for converting that edit into the strongest client-negotiated protocol representation.

When the client advertises `workspace.workspaceEdit.documentChanges = true`, the code-action assembly layer rewrites each document-scoped edit through the language-independent WorkspaceEdit renderer. The resulting `TextDocumentEdit` carries the exact URI and version of the `Document` snapshot used to compute the diagnostic and repair. The renderer never re-reads the document store to discover a newer version.

Clients that do not advertise `documentChanges` retain the existing `WorkspaceEdit.changes` payload unchanged. Quick-fix selection, titles, diagnostic ownership, replacement text, and ranges are identical in both representations.

Both code-action publication paths use the same post-processing helper: the base Nova language server and the final workspace-aware product server. The helper fails closed if a document-scoped quick fix attempts to edit another URI without a captured version. Cross-document repair therefore requires an explicit future design rather than silently borrowing a current document-store version.

Negotiated `codeAction/resolve` preserves the same invariant. The lazy resolve layer captures the already-versioned edit together with the exact diagnostic and workspace snapshot set, removes it from the initial action, and restores that exact edit only after those identities are revalidated.

This keeps edit representation orthogonal to Nova repair policy and extends the repository's generational-identity model into eager and lazy code-action payloads.
