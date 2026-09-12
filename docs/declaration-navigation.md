# Exact-snapshot declaration navigation

The final Nova product advertises `declarationProvider: true` and handles `textDocument/declaration` through the same exact semantic binding used by definition navigation.

For local bindings, parameters, and same-file functions, declaration navigation therefore resolves the declaration owned by the current semantic snapshot. For uniquely resolved cross-file Nova functions it reuses the exact-workspace navigation path, so ambiguous names remain unresolved instead of guessing.

Declaration requests inherit the existing request cancellation and publication guards. A same-version semantic/workspace replacement, close/reopen transition, or other workspace-generation change invalidates an in-flight result and returns `Content modified` rather than publishing a location from an obsolete snapshot generation.

The protocol adapter deliberately does not introduce a second Nova name-resolution implementation: declaration and definition have the same declaration target in the currently supported Nova subset, so both operations share one executable binding pipeline while remaining separate LSP capabilities.
