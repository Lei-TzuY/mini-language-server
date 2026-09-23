# Negotiated diagnostic metadata

The language-independent diagnostic store keeps the complete semantic diagnostic value, including its exact document/semantic snapshot, internal tags, code, source, severity, and span. LSP rendering is a separate session-level protocol boundary and now follows the client's `textDocument.publishDiagnostics` capabilities.

## Push diagnostic versions

`PublishDiagnosticsClientCapabilities.versionSupport` controls the optional `version` field on `textDocument/publishDiagnostics`.

- When `versionSupport = true`, ordinary publications and didChange clears carry the exact captured document version.
- When the capability is absent or false, push notifications omit `version`.
- didClose clears remain unversioned because the document is no longer open.

The diagnostic snapshot itself remains version-bound regardless of what the client can render.

## Diagnostic tags

`PublishDiagnosticsClientCapabilities.tagSupport.valueSet` controls the protocol tags emitted from internal `Diagnostic.tags`.

The repository's bounded internal vocabulary remains:

- `unnecessary` -> LSP `DiagnosticTag.Unnecessary` (1)
- `deprecated` -> LSP `DiagnosticTag.Deprecated` (2)

Only tag values advertised by the client are rendered. Missing, malformed, boolean, string, or unknown values are ignored fail-closed. Internal diagnostic snapshots retain their complete tags even when the client supports none.

The centralized renderer is shared by:

- push diagnostics,
- text-document and workspace pull diagnostics,
- diagnostic copies embedded in Nova code actions.

This prevents the final Nova product from bypassing client negotiation.

## Result identity

Pull-diagnostic result IDs continue to hash the complete internal diagnostic snapshot, including internal tags. Rendering a reduced payload for a client does not erase semantic identity or change the exact-snapshot publication guards. Client capabilities are fixed for the session, so unchanged-report behavior remains deterministic.
