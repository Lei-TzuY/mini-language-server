# Cancellation-aware stdio runtime

The repository exposes one executable LSP host that connects the bounded binary framing layer to the final Nova product server.

## Entry points

After installation:

```bash
mini-language-server
```

The same runtime is available through:

```bash
python -m mini_language_server
```

Both entry points instantiate the final `NovaProductLanguageServer` and use binary process stdin/stdout. No editor-specific wrapper or alternate protocol stack is involved.

## Dispatch loop

The runtime separates framing, one active client request, and foreground document mutation without becoming a general worker pool:

1. one background reader decodes bounded `Content-Length` frames with `MessageReader`;
2. at most one ordinary running-state client request executes on a request worker;
3. while that request is active, `textDocument/didOpen`, `didChange`, `didClose`, negotiated `workspace/didChangeWorkspaceFolders`, `workspace/didChangeConfiguration`, `workspace/didCreateFiles`, `workspace/didDeleteFiles`, `workspace/didRenameFiles`, dynamically registered `workspace/didChangeWatchedFiles`, valid responses to already-delivered pending server-to-client requests, and a coalesced server-request-outbox wakeup may advance on the foreground thread so exact snapshot/configuration guards and bidirectional request dependencies can observe real transport-time input;
4. a live snapshot mutation may overtake queued client requests, but client requests never execute in parallel with each other;
5. the first valid `shutdown` request may overtake queued ordinary client requests while a worker is active only when no earlier deferred non-worker frame must preserve transport order and no live snapshot/configuration mutation has already advanced that active request generation; under that bounded condition the server's existing shutdown boundary retires the worker immediately, runtime stages the exact id for the pre-registration race, and the shutdown response is held until the cancelled worker completion is emitted first; otherwise shutdown itself is deferred in FIFO order so earlier server responses and stale-snapshot outcomes win;
6. a server response is eligible for the live lane only when its id still owns a pending request and that request has already left the local server-request outbox; responses for unknown ids, malformed responses, and guessed responses to requests that have not yet been sent stay outside the live path;
7. `$/cancelRequest` remains an out-of-band control message: once its target request frame has been read, the reader can mark that exact pending request generation cancelled immediately before or during worker execution;
8. the original cancellation notification is replayed through the normal server handle surface at a dispatch boundary so lifecycle and trace behavior remain owned by server layers;
9. when the server-request outbox transitions from empty to non-empty, consumer ownership and trace publication are fixed under the outbox lock before transport is signaled; the foreground loop then drains queued notifications first and tracked server requests second, allowing an active worker to synchronously await a request it created without adding another client-request worker;
10. after every ordinary foreground dispatch or request completion, queued notifications are written first, tracked server-to-client requests second, and a direct response last;
11. every outbound object uses the existing UTF-8 `encode_message()` framing primitive and the batch is flushed once;
12. the loop ends when the server reaches `EXITED` or the transport fails.

The server-to-client request queue/pending map is lock-backed because foreground document mutation or the active request worker may create refresh/configuration/dependency traffic while the request is still executing. Empty-to-non-empty transitions signal the runtime only after any consumer-specific request ownership and trace hook have been published under that same lock. The wakeup callback itself runs outside the lock and is transport-specific; the server core does not depend on the stdio event queue. Repeated requests accumulated in one non-empty batch coalesce behind one wakeup.

A cancel frame may arrive after the request frame has been read but before the handler calls `RequestTracker.start()`. The runtime stages a one-shot cancellation only for request ids it has already read and still owns as pending. `start()` consumes that staged state, and dispatch completion clears any unconsumed stage, so an early cancel cannot poison a later reuse of the same JSON-RPC id. Generic cancellation of unknown ids remains harmless.

## Failure and lifecycle behavior

A normal `shutdown` request followed by an `exit` notification returns the exit code chosen by the server lifecycle. If shutdown arrives while one request worker is active and only deferred ordinary client requests precede it, with no live snapshot/configuration mutation already applied to that active generation, the runtime dispatches shutdown immediately so `RequestTracker.retire_all()` is executable rather than merely theoretical; it normalizes the interrupted request to LSP `Request cancelled`, emits that response before the held shutdown response, and only then resumes deferred traffic under the server's `SHUTDOWN` state. Earlier deferred server responses or an already-applied live mutation preserve their older transport/result causality and force shutdown back into the normal deferred FIFO lane.

EOF before `exit` is treated as an abnormal transport termination and returns a non-zero process status, including when `shutdown` was received but the required `exit` notification never arrived. If a client request worker is still active when EOF is observed, the runtime immediately establishes a transport-abort boundary: the exact transport-owned request generation is cancelled (or pre-cancelled if its worker has not registered the context yet), pending server-to-client requests are retired locally, the notification outbox is closed, and the runtime waits only for cooperative worker termination. The worker's post-failure response and outbound traffic are not flushed.

A `FramingError` uses the same fail-closed transport-abort boundary and returns non-zero without inventing a JSON-RPC response. Once the byte stream has violated the framing contract, the runtime does not guess where a later frame begins.

Stdout failure is the same terminal transport boundary. An `OSError` from either `write()` or the batch `flush()` retires pending server-to-client requests locally, closes notification ownership, detaches the server-request wakeup, and returns non-zero rather than propagating a half-owned transport state. If an ordinary client request worker is active, the exact transport-owned generation is cancelled and the runtime accepts only that worker's cooperative completion; inbound frames observed after the failed write are not dispatched. The input reader is not synchronously joined on this path because a dead output peer may leave stdin blocked indefinitely.

The server's terminal notification/request quiescence remains authoritative. Protocol `exit` and abnormal transport abort in either direction are separate terminal boundaries, but all retire request/outbound ownership so queued traffic cannot be resurrected after the session can no longer deliver it.

## Scope

This milestone keeps exactly one active client-request worker and one foreground snapshot-mutation lane. Document lifecycle changes and workspace-folder scope changes may invalidate the active request through existing exact-generation guards; server-response delivery, `exit`, repeated shutdown, and all other ordinary lifecycle/notification traffic remain serialized behind the worker; formatting-configuration invalidation is the only configuration notification admitted to the live lane; dynamically registered watched-file changes share that foreground lane because they can advance the detached semantic workspace while an exact-workspace request is active, and save-time edits remain generation-guarded before publication. It deliberately does not add asyncio, a multi-request worker pool, parallel client requests, general parallel workspace/configuration mutation, concurrent shutdown/exit, socket transports, process supervision, logging to stdout, or editor-specific launch configuration. Any future broader concurrent host must preserve the same exact-snapshot, ordering, cancellation, lifecycle, and outbound-channel invariants with separate deterministic evidence.

Requests created by the active worker itself can now wake the outbound lane, be framed to the client, and receive a guarded live response while the same worker waits. The runtime detaches that wakeup hook on normal exit, transport abort, and worker-error termination so the server cannot retain a stale transport queue after the session ends.
