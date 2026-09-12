# Exact-snapshot definition LocationLinks

The final Nova product negotiates the LSP `textDocument.definition.linkSupport` client capability. Clients that opt in receive `LocationLink[]` from `textDocument/definition`; clients that do not opt in keep the existing `Location` response shape.

For Nova function navigation, a link carries the exact source occurrence as `originSelectionRange`, the complete target function extent as `targetRange`, and the declared function name as `targetSelectionRange`. Local and parameter definitions retain the same exact semantic binding and use the declaration selection as the bounded target range.

The transformation never re-resolves against newer state. Same-file/local links remain bound to the exact `SemanticSnapshot` that served the definition request, while workspace function links additionally pin the exact workspace semantic snapshot set. Same-version replacement, close/reopen, or other workspace drift therefore produces `Content modified` instead of publishing a link derived from stale identity.

Definition-link requests inherit the existing definition request cancellation checkpoints and ambiguity policy. Cross-file Nova functions are linked only when exactly one current workspace declaration owns the name; ambiguous workspaces continue to return no target rather than guessing.
