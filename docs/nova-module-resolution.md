# Nova Module Resolution Provenance

Nova module paths are resolved against one exact captured workspace plus the captured workspace-folder topology. Resolution is not allowed to consult a newer live workspace while a derived request is being computed.

The resolver keeps three kinds of evidence:

- the path spelling class: relative, current workspace root, named workspace root, or bare workspace path;
- one resolved canonical target URI when the captured evidence proves a unique target;
- every deterministic canonical candidate URI when a bare workspace path matches more than one server-known module.

A bare path therefore has no implicit workspace-folder precedence. Zero matches are unresolved. One canonical match is resolved. Multiple canonical matches are ambiguous. Overlapping workspace folders that reach the same canonical URI do not create false ambiguity.

Only resolved module edges enter visibility, namespace/export traversal, cycle analysis, document links, rename propagation, and file-rename path rewriting. Ambiguous edges remain fail-closed on those surfaces. Import diagnostics retain the candidate evidence instead of discarding it: ordinary import edges report `nova.ambiguous-import`, while wildcard export edges report `nova.ambiguous-export-target`. Candidate related locations are URI-ordered and bind to the exact captured semantic snapshots, so pull diagnostics can expose the same direct related-document ownership.

The existing resolved-target helper remains a compatibility boundary for graph consumers: it projects a resolution to its target URI and returns no target for unresolved or ambiguous outcomes. This prevents callers from accidentally choosing one ambiguity candidate.

This phase still does not define precedence-ordered package roots, external package registries, remote module providers, cross-authority resolution, or stable package identities. Those require an explicit search-path/configuration authority rather than guessing from the current workspace.
