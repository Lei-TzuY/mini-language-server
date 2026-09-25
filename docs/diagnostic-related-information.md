# Exact-snapshot diagnostic related information

The language-independent diagnostic model supports bounded related source locations through `DiagnosticRelatedInformation`.

Each related location contains:

- the source URI,
- one Python-offset `Span`,
- a non-empty explanatory message.

Same-URI related locations remain implicitly bound to the diagnostic's primary semantic snapshot. A cross-URI related location has two explicit ownership modes. When it carries a target `SemanticSnapshot`, `DiagnosticStore.publish` validates that the target semantic is current, that its URI matches the related URI, and that the related span fits the target document; diagnostic `get`, commit, and publication then guard that semantic identity alongside the primary parent. Replacing such a target therefore makes the old diagnostic snapshot stale even when the primary document and numeric versions are unchanged.

A cross-URI related location may instead explicitly set `location_only=True` while omitting `semantic`. Unmarked cross-URI locations still require an exact semantic snapshot. The opt-in location-only mode deliberately makes no live-semantic freshness claim, is excluded from `DiagnosticSnapshot.related_semantics`, and therefore never creates a `relatedDocuments` dependency. Producers may use it only when the location comes from separately guarded external evidence whose ownership must not be inserted into the live semantic store. The Nova workspace ambiguity publisher uses this form for detached closed-module candidates after committing against the exact captured workspace; detached pull diagnostics retain the full semantic-parent form because their ownership is validated by the detached workspace pull transaction.

## Protocol negotiation

The server reads `textDocument.publishDiagnostics.relatedInformation` during initialize.

When the client advertises support, the centralized diagnostic renderer emits standard LSP `relatedInformation` entries. Push diagnostics, text/workspace pull diagnostics, and diagnostic copies embedded in code actions all use the same renderer.

When support is absent or false, the protocol payload omits `relatedInformation`, but the internal `DiagnosticSnapshot` retains the complete related-location tuple.

Pull diagnostic result IDs hash related URI/span/message values, so adding or removing related locations changes semantic result identity even for a client that cannot render them.

## Pull diagnostic related documents

For `textDocument/diagnostic`, only exact cross-file semantic parents define the direct LSP `relatedDocuments` dependency set; location-only metadata remains inside the diagnostic and does not imply a pull dependency. The primary document keeps its normal `previousResultId` full/unchanged behavior. Each direct related document is returned as a deterministic full diagnostic report because the request carries no previous result ID for those dependency reports.

Related-document reports are rendered from each dependency's own exact ownership snapshot and use the same negotiated position encoding as ordinary pull diagnostics. For an open primary, dependencies remain exact live document/diagnostic snapshots. For a detached closed primary, dependencies may be mixed: open dependencies are guarded by their live Document/DiagnosticSnapshot identities, while closed dependencies come from the same captured detached workspace diagnostic set and carry `null:<digest>` result lineage. Without a `partialResultToken`, they remain inline under the primary report's `relatedDocuments` field. With a valid token, the primary full/unchanged report stays in the final response while direct related documents are emitted as deterministic URI-ordered `$/progress` chunks using the standard `{ relatedDocuments: ... }` partial-result shape. The final response and every partial chunk are committed only while the primary document, every direct related document, the primary diagnostic snapshot, and every available related diagnostic snapshot remain exact-current. A dependency edit during render or before commit therefore fails the whole request with `Content modified` and emits no partial data instead of mixing generations.

Only direct cross-file parents are exposed. A related document's own dependencies are not recursively nested into the primary response; clients can pull those documents independently if needed.

## Nova duplicate declarations

Nova duplicate function, parameter, and local diagnostics attach all other declarations in the same duplicate group as related information. Exact-workspace `nova.ambiguous-function` diagnostics also attach every candidate function declaration, including candidates in other open workspace files.

For example, each `nova.duplicate-function` diagnostic points to the other declaration(s) with a message identifying the conflicting function declaration. The same rule applies to duplicate parameters within one function and duplicate locals within one function scope.

This makes the existing duplicate-declaration quick fixes and push/pull diagnostics navigable without changing the collision or rename policy.

Cross-file related locations render their ranges from the target snapshot's own document text while still using the session's negotiated position encoding. Ambiguous-call candidates are produced only from the exact workspace declaration set used by the workspace diagnostic transaction, so a provider edit, close/reopen, same-version semantic replacement, or workspace-scope mutation cannot leave a navigable stale candidate behind.
