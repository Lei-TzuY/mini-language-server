# Server-initiated requests and diagnostic refresh

The language-independent server core now supports tracked JSON-RPC requests from the server to the client. Server request IDs use a dedicated monotonic namespace, queued requests are drained independently from notifications, and a pending request is retired only after a response-shaped JSON-RPC message with exactly one of `result` or `error` is received. Unknown response IDs are ignored, malformed responses do not consume pending state, and malformed client requests without a method remain normal `Invalid Request` errors.

The first production consumer is LSP `workspace/diagnostic/refresh`. The second is `workspace/codeLens/refresh` for Nova's exact-workspace reference-count lenses. Exact-workspace Nova inlay hints also consume `workspace/inlayHint/refresh` so cross-file parameter/type hint changes invalidate visible hints. A result-bearing `workspace/configuration` consumer demonstrates payload delivery: after core response validation retires the pending request, a consumer hook receives the exact result/error payload and may apply it under its own generation guards. Each refresh method is enabled only when the matching workspace client capability advertises `refreshSupport = true`; diagnostics additionally require pull-diagnostic support, and inlay-hint refresh additionally requires text-document inlay-hint support. Nova reports `diagnosticProvider.interFileDependencies = true` because declarations and signatures in one open document can change call/type diagnostics in another open document.

Refresh emission is deliberately conservative. A global refresh is queued only after:
- the exact workspace snapshot identity has changed;
- at least one other open workspace document could be affected;
- the final exact-workspace diagnostic compare-and-commit succeeds.

Only one request per refresh method may remain pending at a time. Additional qualifying workspace changes coalesce until the client returns either a successful result or an error response, after which a later change may queue another request. A diagnostic refresh waits for the final exact-workspace diagnostic commit; CodeLens refresh depends directly on a committed workspace identity change. Single-document edits do not trigger either global request.

The refresh request carries no params, matching the LSP request shape. It does not replace deterministic pull result IDs or exact-snapshot validation: the client is merely told to re-pull, and each subsequent `textDocument/diagnostic` or `workspace/diagnostic` request still runs through the existing document/diagnostic/workspace generation guards.

This outbound-request substrate is intentionally generic. Diagnostic and CodeLens refreshes reuse the same tracked lifecycle independently, while workspace configuration consumes validated response payloads through the shared completion hook. Future semantic-token, inlay-hint, configuration, or other request methods should add only their own capability negotiation, payload validation, and conservative invalidation policy rather than creating feature-specific transport queues.
