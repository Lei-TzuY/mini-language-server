# Terminal outbound notification quiescence

The server lifecycle now closes the notification channel as a terminal ownership boundary in addition to retiring client-to-server and server-to-client requests.

## Shutdown

A successful client `shutdown` request performs three coordinated actions before entering the shutdown state:

1. retire every active client-to-server request context;
2. retire every pending server-to-client request, emitting standard `$/cancelRequest` only for requests that were already delivered to the client;
3. terminally close the notification outbox.

Closing the notification outbox discards queued diagnostics, progress, trace, refresh-adjacent notifications, and other ordinary server notifications that no longer have a live session to observe them. Only lifecycle-required `$/cancelRequest` notifications produced while retiring already-delivered server requests are preserved for transport after the shutdown response.

## Exit

`exit` is stricter. It retires both request directions locally, closes the notification outbox, and preserves no new protocol traffic. Any notifications that were queued but not yet drained are discarded.

## Concurrency invariant

Notification publication and terminal close share one re-entrant lock. The lifecycle gate and append happen under the same lock, so a worker that began racing with shutdown cannot read an open state and append after the close boundary. A worker blocked behind the close observes the terminal state and its notification is rejected.

The same primitive owns ordinary notifications, `$/progress`, diagnostics, and trace output. Trace emission still bypasses `_queue_notification` to avoid recursively tracing `$/logTrace`, but it no longer bypasses the lifecycle-gated append primitive.

## Deliberate scope

This milestone does not create a new transport or scheduler. It closes the existing in-memory outbound notification channel consistently with the request-retirement work. Ordinary draining while the session is running is unchanged, and shutdown still emits the protocol-required cancellation notifications for server requests that the client has already seen.
