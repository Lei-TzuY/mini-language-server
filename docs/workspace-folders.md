# Workspace folder scoped tooling

The server supports LSP workspace-folder scope as the semantic universe for cross-file Nova tooling.

## Scope establishment

- `initialize.workspaceFolders` establishes the initial ordered folder set when supplied.
- `initialize.rootUri` is used as a bounded fallback when `workspaceFolders` is absent.
- When neither field establishes a scope, the server preserves the historical compatibility mode in which every open document may participate in workspace tooling.
- Clients advertising `capabilities.workspace.workspaceFolders = true` receive `capabilities.workspace.workspaceFolders` with `supported: true` and `changeNotifications: true`.

## Dynamic folder lifecycle

`workspace/didChangeWorkspaceFolders` updates the scope only for clients that negotiated folder support. Already-open Nova documents are reclassified immediately: newly included documents enter the shared workspace index, removed documents leave it, and cross-file diagnostics are reconciled against the new exact workspace.

URI containment is segment-aware and uses RFC 3986 generic URI equivalence where it is safe for workspace identity. Scheme and host comparison are case-insensitive, percent-encoding hex digits are normalized, and percent-encoded unreserved characters such as `%7E` are equivalent to their literal form. Reserved delimiters are not decoded: `%2F` never becomes a path separator. Path character case remains significant, so the server does not guess filesystem case rules from the URI. A folder such as `file:///work/app` contains `file:///work/app/main.nova` but not `file:///work/application/main.nova`.

Equivalent folder spellings share one internal scope identity. Initial folder lists reject canonical duplicates, and dynamic removal can identify a configured folder through an equivalent URI spelling. The original client-supplied folder URI remains the public value returned by folder snapshots and `scope_uri_for()`, so configuration `scopeUri` requests are not silently rewritten.

Out-of-scope documents remain open and retain their document/syntax/symbol/semantic snapshots and single-document Nova analysis. They do not contribute declarations, references, calls, CodeLens counts, workspace symbols, or other cross-file facts until their folder enters the scope.

## Exactness and stale-result suppression

Workspace membership carries an independent generation. `WorkspaceSymbolIndex.snapshots()` returns a tuple-compatible capture containing both exact semantic snapshot identities and the workspace generation. Complete-workspace publications reject any generation transition, including the ABA case where a semantic snapshot is removed and later re-added unchanged.

Workspace-folder topology is also part of that complete-workspace generation. Every effective folder-scope transition invalidates previously captured complete workspace queries even when the exact indexed URI set and every `SemanticSnapshot` object remain unchanged, such as adding a nested workspace folder around an already-indexed document. Deliberately partial subset commits continue to validate only their named snapshot identities; callers whose semantics depend on complete scope must use the complete snapshot guard.

Workspace-folder scope retains its own generation guard as well. Workspace diagnostics capture both the folder generation and the exact scoped document/diagnostic snapshots; a folder transition during a request returns `Content modified` rather than publishing a report for the previous universe.

Workspace-wide symbol search uses the complete workspace snapshot guard rather than only the declarations present in the result, so adding a previously unseen document invalidates an in-flight search even when existing result objects are still current.

## Configuration scope integration

Workspace-folder URIs also define the standard `scopeUri` universe for save-time formatting configuration. When multiple configured folders contain a document, the most specific folder wins. Documents outside the active folders use the global configuration fallback. Dynamic folder changes advance the formatting-configuration generation so a pending response for the old scope set cannot overwrite the new one.

## Refresh integration

When folder membership changes the workspace identity, already-negotiated workspace refresh channels are reused. Pull-diagnostic clients receive `workspace/diagnostic/refresh`, reference-CodeLens clients receive `workspace/codeLens/refresh`, and supporting inlay-hint clients receive `workspace/inlayHint/refresh`. Existing request coalescing and response retirement continue to apply.

Configured local `file:` workspace folders now also provide a bounded closed-file declaration index after `initialized`. Detached disk snapshots contribute to workspace tooling without entering the live `DocumentStore`; open buffers override equivalent disk identities and `didClose` restores current disk content. Dynamic folder changes reconcile both open and detached contributions. Filesystem watching, remote providers, and closed-file diagnostics remain outside this phase; see [`closed-workspace-index.md`](closed-workspace-index.md).
