# Closed-file workspace indexing

The Nova workspace can index a bounded set of local `*.nova` files that are inside configured workspace folders even when the editor has not opened those files.

## Ownership model

Closed files are never inserted into the live `DocumentStore`. Discovery builds detached immutable syntax/symbol/semantic snapshots and contributes them only to `WorkspaceSymbolIndex`. This preserves the existing open-document ownership chain: document versions, push/pull diagnostics, incremental edits, and live semantic stores continue to describe editor-owned buffers only.

An open document always wins over a disk snapshot with the same RFC-safe URI identity. Opening an equivalent URI removes the detached contribution before the live buffer is indexed. Closing the buffer rereads the current disk file and restores a detached contribution when the file still exists and remains in scope.

Closed snapshots never publish push diagnostics and are not inserted into the live DiagnosticStore. They do participate in negotiated `workspace/diagnostic` pulls through ephemeral immutable diagnostic snapshots: adapter-local duplicate/unresolved-name diagnostics are retained from the detached parse, while same-file function-call errors are replaced by unresolved/ambiguous resolution against the exact captured combined workspace. Closed reports use `version: null` and deterministic `null:<digest>` result IDs, so no detached internal version is presented as a filesystem concurrency claim. Their declarations and parse-tree call sites also participate in existing workspace symbol search, definition/hover/completion, reference scans, call hierarchy, collision checks, and cross-file call resolution for open documents.

## Filesystem boundary

Discovery is deliberately local and bounded:

- only configured local `file:` workspace folders are scanned;
- only files ending in `.nova` are considered;
- symlink roots/files are skipped rather than assigned guessed filesystem identity;
- each file is limited to 2 MiB;
- at most 2048 closed Nova files are indexed per scan;
- source bytes must decode as UTF-8;
- non-local authorities, query/fragment-bearing file URIs, unreadable files, invalid UTF-8, and unsupported shapes fail closed.

URI deduplication reuses the workspace-folder RFC-safe identity rules. Scheme/host case and unreserved percent encoding are normalized, reserved delimiters and path case are not guessed, and the index does not claim realpath, inode, or platform-specific case equivalence.

## Refresh lifecycle

A bounded filesystem scan runs after the LSP `initialized` notification when a workspace folder/root scope exists. The closed index is also reconciled after workspace-folder changes and negotiated `workspace/didCreateFiles`, `workspace/didDeleteFiles`, and `workspace/didRenameFiles`. Create/delete notifications are validated as complete URI batches and only trigger a scan when at least one in-scope local `.nova` URI can affect the detached index. `didClose` restores the single relinquished disk source directly.

The server does not watch the filesystem in this phase. External edits made without a matching negotiated resource notification remain outside the server-known workspace snapshot until another documented lifecycle transition refreshes the index. Read-only tooling therefore describes the exact server-known indexed snapshot, not an unobserved later disk state. Workspace diagnostic pulls capture the complete workspace semantic set and reject publication with `Content modified` if folder or workspace identity changes before commit.

Source-mutating rename is stricter. Before publishing a WorkspaceEdit that was derived from detached sources, the server rereads every closed input and compares its current UTF-8 text with the captured snapshot. Drift refreshes the closed index and returns LSP `Content modified` instead of applying stale source ranges.

## WorkspaceEdit versions

When a client negotiates `WorkspaceEdit.documentChanges`, editor-owned files keep their exact captured integer versions. Detached closed files use the LSP `OptionalVersionedTextDocumentIdentifier` form with `version: null`; the server never invents version `0` as a concurrency claim for disk files.

Legacy clients that do not negotiate `documentChanges` continue to receive the existing `changes` representation.

## Deliberate nonclaims

This milestone is not a general filesystem service and does not claim full closed-file diagnostic parity. Closed files still do not receive push diagnostics, `textDocument/diagnostic`, code actions, or the complete stack of product-only argument/type/return/data-flow diagnostics. Filesystem watchers, remote workspace providers, symlink identity, module/import path rewriting, and permission/preflight guarantees for closed-file resource renames also remain separate executable phases.
