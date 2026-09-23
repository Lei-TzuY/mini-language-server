# Server-request supersession

The language-independent server-to-client request substrate can retire a pending request when a consumer proves that the request payload is already obsolete.

This is intentionally different from ordinary refresh coalescing. A pending `workspace/diagnostic/refresh`, `workspace/inlayHint/refresh`, `workspace/codeLens/refresh`, or `workspace/semanticTokens/refresh` can still tell the client to re-pull the latest state, so those requests remain coalesced until the client responds. A `workspace/configuration` request, by contrast, contains a concrete list of scope items captured for one formatting-configuration generation. Once the workspace/configuration generation changes, that exact payload can no longer represent the requested state.

## Generic retirement lifecycle

`_cancel_server_request(id)` first removes the request from the pending-response map.

If the request is still present in the local server-request outbox, the client has not observed it yet. The server retracts it locally and emits no protocol cancellation.

If the request has already been drained from the outbox, the server queues the standard `$/cancelRequest` notification for that request id. The request remains retired locally immediately; cancellation acknowledgement is not required.

A late client response to a retired id is ignored because the id no longer owns a pending request record. Consumers receive `_server_request_cancelled(id, method)` so any request-specific metadata can be discarded at the same retirement boundary.

`_cancel_pending_server_requests(method)` applies the same lifecycle to every currently pending request for one method.

## Formatting configuration integration

Every formatting configuration request records the exact formatting generation and deterministic scope tuple used to build its `workspace/configuration` items.

When `workspace/didChangeConfiguration` or workspace-folder membership changes:

1. increment the formatting configuration generation;
2. retire all pending `workspace/configuration` requests;
3. discard their per-request generation/scope records;
4. immediately queue one request for the new generation.

If an obsolete request had already been sent, the client receives `$/cancelRequest`. If it was still local, only the fresh request remains in the outbox.

The last valid formatting settings remain usable until a current-generation response arrives. A late response from the retired generation cannot overwrite them and cannot trigger another request.

Tracing observes real outbound cancellation notifications through the generic notification queue. Local retraction of a request that never left the server produces no fake outbound cancellation trace.

## Lifecycle retirement

A successful client `shutdown` request is also a hard ownership boundary for server-initiated requests. The request outbox, pending-response map, cancellation, response retirement, and terminal close share one re-entrant lock. Shutdown closes the request channel under that lock before detaching pending ownership, so a late worker cannot enqueue a fresh request after retirement.

Requests that already left the local outbox receive standard `$/cancelRequest`; requests still queued locally are retracted without fake protocol traffic. Consumer-owned per-request metadata is discarded while the same request lock still owns the transition. A concurrent client response either completes its consumer callback before shutdown acquires the lock, or loses ownership to terminal retirement and is ignored. The server therefore cannot acknowledge shutdown and then run an older response callback that mutates configuration or registration state.

After the channel closes, `_queue_server_request(...)` returns `None` without allocating a request id, adding pending ownership, or emitting trace traffic. Consumers that persist per-request metadata record it only after a request id was accepted. Refresh consumers that do not need the id naturally become no-ops at the terminal boundary.

An `exit` notification performs final local retirement without emitting new cancellation traffic because no further protocol exchange is expected. This also covers abnormal exit without a preceding shutdown.

Session termination also retires the opposite request direction. Before shutdown is acknowledged, the generic `RequestTracker` marks every in-flight client-to-server request context cancelled and detaches it from the active map. Workers that resume after the lifecycle boundary therefore fail their existing cancellation checkpoints instead of successfully publishing a late semantic result. `exit` applies the same terminal request-context retirement even when the client skipped shutdown.

This capability does not otherwise change refresh coalescing or generation supersession. Supersession is used when a payload becomes stale during normal operation; lifecycle retirement closes all remaining request ownership when the session itself ends.
