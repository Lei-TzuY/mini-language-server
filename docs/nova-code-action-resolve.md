# Nova code-action resolve

The final Nova product negotiates `codeAction/resolve` only when the client advertises `textDocument.codeAction.resolveSupport.properties` containing `edit`.

When negotiated, `textDocument/codeAction` still computes and validates the quick fix against the exact current diagnostic and workspace snapshots, but the response omits the `edit`. The action instead carries opaque `novaCodeActionResolve` data. The server retains the exact original action together with the diagnostic snapshot and workspace snapshot set that produced it.

`codeAction/resolve` restores the captured `WorkspaceEdit`; it never regenerates the edit from a newer document or workspace. Resolution succeeds only while the exact diagnostic and workspace identities remain current. Same-version diagnostic replacement, document change, close/reopen, workspace replacement, or another stale lineage returns LSP `Content modified`. Cancellation is checked again at the final publication boundary.

Clients that do not advertise `edit` resolve support keep the existing eager quick-fix behavior and `codeActionProvider: true`. Nova repair policy remains in the product layer; the generic document, semantic, diagnostic, and workspace stores remain language-independent.