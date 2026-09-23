# Workspace diagnostic partial results

The language-independent pull-diagnostic product supports LSP `partialResultToken` on `workspace/diagnostic`.

When a request omits the token, behavior is unchanged: the final response contains the complete deterministic `items` array.

When a request supplies a valid LSP progress token (string or integer), the server:

1. captures one exact workspace-folder scope and complete matching open-document set;
2. computes every document diagnostic report, including `full` / `unchanged` result-id behavior;
3. validates request cancellation and the exact folder, document, diagnostic, and related-semantic parents;
4. only after all freshness guards succeed, queues standard `$/progress` notifications in deterministic URI order;
5. chunks reports in groups of 16 as `{"items": [...]}`;
6. returns a final workspace diagnostic report with an empty `items` array so reports are not duplicated.

This repository intentionally does not emit progress chunks before the exact commit boundary. The request handler is synchronous, and preserving the repository's stale-result invariant is more important than pretending to stream speculative data that might later fail freshness validation. A cancelled or stale request therefore emits no partial-result progress at all.

Malformed tokens, including booleans, null, arrays, and objects, are rejected as invalid params. Both string and integer tokens are preserved exactly in the standard `$/progress` notification.

The same exact workspace membership rule applies to partial and non-partial pulls: adding a new in-scope URI, replacing or closing a captured document, changing folder scope, replacing a diagnostic snapshot, or replacing a cross-file related semantic parent rejects publication. Out-of-scope document changes do not invalidate a scoped workspace pull.
