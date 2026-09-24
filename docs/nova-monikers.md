# Nova project function monikers

The Nova workspace layer supports the standard LSP `textDocument/moniker` request for one deliberately bounded identity class: uniquely resolved workspace functions inside a configured workspace-folder project.

Clients opt in through `textDocument.moniker`. Supporting sessions advertise `monikerProvider: true`. The request uses the same exact semantic function query as workspace navigation, so a position must identify a Nova function declaration or call before moniker resolution begins.

A function receives one moniker only when the current workspace index contains exactly one function declaration with that name and the declaration belongs to a workspace folder captured for the request. The returned moniker is:

- `scheme: "nova"`
- `identifier: <function name>`
- `unique: "project"`
- `kind: "local"`

The uniqueness claim is intentionally limited to the project. The server does not claim scheme-wide or global identity because Nova currently has no package/module namespace that could justify such a guarantee. The `local` kind likewise states only that the symbol is project-local; cross-file visibility inside the active project does not imply an exported external API.

Unscoped sessions, documents outside configured workspace folders, ambiguous duplicate function names, non-function symbols, and unresolved calls return no moniker rather than guessing. A uniquely resolved selective-import alias reuses the canonical declaration's project moniker and identifier rather than minting a second identity for the local binding. This also keeps moniker semantics aligned with the existing conservative workspace navigation model.

The request captures three independent generations before publication: the exact document semantic snapshot, the complete workspace symbol snapshot set, and the workspace-folder snapshot. Any same-version semantic replacement, cross-file symbol change, folder add/remove, close/reopen transition, or other captured-generation drift rejects publication with LSP `Content modified`. Normal request cancellation returns `Request cancelled`.

Workspace-folder snapshots expose their own pure `scope_uri_for()` query so project identity is derived from the captured folder set instead of a later live lookup. The existing mutable `WorkspaceFolderSet.scope_uri_for()` delegates to the same snapshot rule, keeping scope selection consistent for monikers and configuration consumers.
