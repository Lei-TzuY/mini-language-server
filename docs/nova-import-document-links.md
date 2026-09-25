# Nova import document links

The bounded Nova local-file import phase exposes negotiated LSP `textDocument/documentLink` navigation for editor-owned Nova buffers, including scoped `@/path.nova` current-root imports and unique `@name/path.nova` workspace-folder-name imports.

When the client advertises `textDocument.documentLink`, the server advertises `documentLinkProvider` with `resolveProvider: false`. Each supported top-level import whose target exists in the exact server-known workspace becomes one link whose range is the exact import-path span and whose target is the URI owned by the matching workspace semantic snapshot. `@/` targets are resolved against the importer's most-specific configured workspace folder. `@name/` targets resolve only when exactly one configured LSP workspace folder has that exact name; duplicate names are ambiguous and produce no link.

Publication captures both the primary open `SemanticSnapshot` and the complete `WorkspaceSymbolIndex` snapshot set. A document replacement, close/reopen, target addition/removal, watcher reconciliation, folder-driven workspace change, or other relevant workspace generation change before commit returns LSP `Content modified`; cancellation returns `Request cancelled`.

Unresolved imports, unsupported URI shapes, and imports that cannot resolve through the bounded same-authority local-file resolver do not produce links. Closed files remain available to pull diagnostics and may serve as link targets, but a detached closed importer does not itself own a `textDocument/documentLink` request because document-link ranges are editor-document tooling.

Document links remain a navigation surface only. The next bounded namespace phase adds direct-import function visibility for semantic call tooling, but this document-link contract itself still does not introduce arbitrary precedence-ordered or external package search lists beyond bounded `@/`, exact unique `@name/`, and unique captured bare-workspace lookup, wildcard imports, non-function imported namespaces, remote-provider modules, cross-authority resolution, or lazy document-link resolution.
