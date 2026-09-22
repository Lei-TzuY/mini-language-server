# Server-initiated requests and diagnostic refresh

The language-independent server core now supports tracked JSON-RPC requests from the server to the client. Server request IDs use a dedicated monotonic namespace, queued requests are drained independently from notifications, and a pending request is retired only after a response-shaped JSON-RPC message with exactly one of `result` or `error` is received. Unknown response IDs are ignored, malformed responses do not consume pending state, and malformed client requests without a method remain normal `Invalid Request` errors.

The first production consumer is LSP `workspace/diagnostic/refresh`. The request is enabled only when the client advertises both pull diagnostics and `workspace.diagnostics.refreshSupport = true`. Nova reports `diagnosticProvider.interFileDependencies = true` because declarations and signatures in one open document can change call/type diagnostics in another open document.

Refresh emission is deliberately conservative. A global refresh is queued only after:
- the exact workspace snapshot identity has changed;
- at least one other open workspace document could be affected;
- the final exact-workspace diagnostic compare-and-commit succeeds.

Only one diagnostic refresh may remain pending at a time. Additional workspace changes coalesce until the client returns either a successful result or an error response, after which a later qualifying workspace change may queue a new refresh. Single-document edits do not trigger the global request.

The refresh request carries no params, matching the LSP request shape. It does not replace deterministic pull result IDs or exact-snapshot validation: the client is merely told to re-pull, and each subsequent `textDocument/diagnostic` or `workspace/diagnostic` request still runs through the existing document/diagnostic/workspace generation guards.

This outbound-request substrate is intentionally generic. Future server-initiated refresh methods should reuse the same tracked request lifecycle and add their own capability negotiation and conservative invalidation policy rather than creating feature-specific request queues.
