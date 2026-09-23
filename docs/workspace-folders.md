# Workspace folder scoped tooling

The server supports LSP workspace-folder scope as the semantic universe for cross-file Nova tooling.

## Scope establishment

- `initialize.workspaceFolders` establishes the initial ordered folder set when supplied.
- `initialize.rootUri` is used as a bounded fallback when `workspaceFolders` is absent.
- When neither field establishes a scope, the server preserves the historical compatibility mode in which every open document may participate in workspace tooling.
- Clients advertising `capabilities.workspace.workspaceFolders = true` receive `capabilities.workspace.workspaceFolders` with `supported: true` and `changeNotifications: true`.

## Dynamic folder lifecycle

`workspace/didChangeWorkspaceFolders` updates the scope only for clients that negotiated folder support. Already-open Nova documents are reclassified immediately: newly included documents enter the shared workspace index, removed documents leave it, and cross-file diagnostics are reconciled against the new exact workspace.

URI containment is segment-aware. A folder such as `file:///work/app` contains `file:///work/app/main.nova` but not `file:///work/application/main.nova`. Scheme and authority must also match.

Out-of-scope documents remain open and retain their document/syntax/symbol/semantic snapshots and single-document Nova analysis. They do not contribute declarations, references, calls, CodeLens counts, workspace symbols, or other cross-file facts until their folder enters the scope.

## Exactness and stale-result suppression

Workspace membership carries an independent generation. `WorkspaceSymbolIndex.snapshots()` returns a tuple-compatible capture containing both exact semantic snapshot identities and the workspace generation. Complete-workspace publications reject any generation transition, including the ABA case where a semantic snapshot is removed and later re-added unchanged.

Workspace-folder scope has its own generation guard. Workspace diagnostics capture both the folder generation and the exact scoped document/diagnostic snapshots; a folder transition during a request returns `Content modified` rather than publishing a report for the previous universe.

Workspace-wide symbol search uses the complete workspace snapshot guard rather than only the declarations present in the result, so adding a previously unseen document invalidates an in-flight search even when existing result objects are still current.

## Configuration scope integration

Workspace-folder URIs also define the standard `scopeUri` universe for save-time formatting configuration. When multiple configured folders contain a document, the most specific folder wins. Documents outside the active folders use the global configuration fallback. Dynamic folder changes advance the formatting-configuration generation so a pending response for the old scope set cannot overwrite the new one.

## Refresh integration

When folder membership changes the workspace identity, already-negotiated workspace refresh channels are reused. Pull-diagnostic clients receive `workspace/diagnostic/refresh`, reference-CodeLens clients receive `workspace/codeLens/refresh`, and supporting inlay-hint clients receive `workspace/inlayHint/refresh`. Existing request coalescing and response retirement continue to apply.

This milestone scopes the existing open-document workspace model. It does not scan closed files from disk, watch filesystem roots, or synthesize semantic snapshots for files the client has not opened.
