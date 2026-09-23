# Same-snapshot diagnostic related information

The language-independent diagnostic model supports bounded related source locations through `DiagnosticRelatedInformation`.

Each related location contains:

- the source URI,
- one Python-offset `Span`,
- a non-empty explanatory message.

This first slice deliberately restricts related locations to the same URI as the diagnostic's exact semantic snapshot. `DiagnosticStore.publish` validates that invariant and rejects out-of-range related spans. That keeps related locations under the same document/syntax/symbol/semantic generation guard as the primary diagnostic instead of pretending that an arbitrary cross-file location is fresh without a workspace-generation dependency.

## Protocol negotiation

The server reads `textDocument.publishDiagnostics.relatedInformation` during initialize.

When the client advertises support, the centralized diagnostic renderer emits standard LSP `relatedInformation` entries. Push diagnostics, text/workspace pull diagnostics, and diagnostic copies embedded in code actions all use the same renderer.

When support is absent or false, the protocol payload omits `relatedInformation`, but the internal `DiagnosticSnapshot` retains the complete related-location tuple.

Pull diagnostic result IDs hash related URI/span/message values, so adding or removing related locations changes semantic result identity even for a client that cannot render them.

## Nova duplicate declarations

Nova duplicate function, parameter, and local diagnostics attach all other declarations in the same duplicate group as related information.

For example, each `nova.duplicate-function` diagnostic points to the other declaration(s) with a message identifying the conflicting function declaration. The same rule applies to duplicate parameters within one function and duplicate locals within one function scope.

This makes the existing duplicate-declaration quick fixes and push/pull diagnostics navigable without changing the collision or rename policy.

Cross-file related information remains deferred until a diagnostic snapshot can capture and validate the target workspace identity as part of its freshness proof.
