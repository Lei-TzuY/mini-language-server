# Nova Module Resolution Provenance

Nova module paths are resolved against one exact captured workspace plus the captured workspace-folder topology. Resolution is not allowed to consult a newer live workspace while a derived request is being computed.

The resolver keeps three kinds of evidence:

- the path spelling class: relative, current workspace root, named workspace root, or bare workspace path;
- one resolved canonical target URI when the captured evidence proves a unique target;
- every deterministic canonical candidate URI when a bare workspace path matches more than one server-known module.

A bare path has no implicit workspace-folder precedence. Without an explicit search-root policy, zero matches are unresolved, one canonical match is resolved, and multiple canonical matches are ambiguous. Overlapping workspace folders that reach the same canonical URI do not create false ambiguity.

Only resolved module edges enter visibility, namespace/export traversal, cycle analysis, document links, rename propagation, and file-rename path rewriting. Ambiguous edges remain fail-closed on those surfaces. Import diagnostics retain the candidate evidence instead of discarding it: ordinary import edges report `nova.ambiguous-import`, while wildcard export edges report `nova.ambiguous-export-target`. Candidate related locations are URI-ordered and bind to the exact captured semantic snapshots, so pull diagnostics can expose the same direct related-document ownership.

The existing resolved-target helper remains a compatibility boundary for graph consumers: it projects a resolution to its target URI and returns no target for unresolved or ambiguous outcomes. This prevents callers from accidentally choosing one ambiguity candidate.

## Initialization-authoritative search roots

Clients may opt into one bounded precedence policy with `initializationOptions.nova.moduleSearchRoots`. The value is an ordered array of local `file:` workspace-folder URIs. Each entry is canonicalized through the same workspace URI-identity rules as module lookup, duplicates keep the first occurrence, and only entries that exactly match an active workspace folder participate. Unlisted workspace folders are excluded from bare lookup. An empty or malformed explicit list therefore fails closed instead of falling back to the implicit all-folder search.

When this policy is present, bare lookup evaluates the configured active roots in that exact order. The first root that owns the requested server-known module becomes `target_uri`; `candidate_uris` still retains every matching configured root in precedence order so provenance is not discarded. This converts a previously ambiguous bare path into one explicit resolved edge without changing relative, `@/`, or `@name/` resolution. Import-path completion calls the same resolver, so it exposes only labels that resolve to the same precedence-selected target.

The initialization value remains the fallback policy. When the client also advertises `workspace.configuration = true`, explicit initialization opt-in enables one runtime override section, `mini-language-server.nova.moduleSearchRoots`, carried in the same tracked `workspace/configuration` request used by save-formatting configuration. `workspace/didChangeConfiguration` never trusts its arbitrary settings payload: it advances the module-policy generation, retires the obsolete configuration request, and waits for a validated server-request response. A `null` module-root value restores the initialization fallback; an invalid explicit value fails closed to an empty search set. Applying a changed policy advances the complete workspace-query generation before recomputing open diagnostics and notifying workspace-dependent refresh hooks, so requests captured under the previous authority cannot publish afterward.

Later workspace-folder add/remove events may still activate or deactivate configured root identities and already advance the existing workspace-folder generation, so derived requests remain guarded by the same exact folder/workspace commit boundaries.

This phase still does not define search roots outside active workspace folders, package registries, remote module providers, cross-authority resolution, or stable package identities. Those require a broader provider authority rather than guessing from the filesystem.
