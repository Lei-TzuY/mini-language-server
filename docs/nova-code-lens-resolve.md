# Nova CodeLens resolve

Nova reference-count CodeLens items are resolved lazily through `codeLens/resolve`.

When a client advertises `textDocument.codeLens`, the server publishes `codeLensProvider.resolveProvider = true`. The initial `textDocument/codeLens` response contains the function range and an opaque resolve token, but it deliberately omits the executable command. This avoids computing cross-file reference counts until the editor actually resolves a visible lens.

Each token captures the exact semantic snapshot for the declaration document and the exact workspace snapshot set used when the lens was produced. `codeLens/resolve` computes the reference count only from those captured snapshots, then re-validates both identities at the final publication boundary. Same-version replacement, incremental workspace change, close/reopen, or any other snapshot drift fails with LSP `Content modified` instead of publishing a command derived from stale state.

A successful resolve adds the existing `mini-language-server.showReferences` command with its deterministic singular/plural reference-count title. `workspace/executeCommand` remains responsible for returning the current exact-workspace reference locations.

Focused protocol regressions cover cross-file counts, ambiguity, incremental changes, same-version replacement, close/reopen, and cancellation at both CodeLens and resolve publication boundaries.