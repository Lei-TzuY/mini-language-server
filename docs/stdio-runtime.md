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
3. while that request is active, `textDocument/didOpen`, `didChange`, `didClose`, and negotiated `workspace/didChangeWorkspaceFolders` may dispatch on the foreground thread so exact document/syntax/semantic/workspace-scope guards can observe real transport-time mutation;
4. a live snapshot mutation may overtake queued client requests, but client requests never execute in parallel with each other;
5. shutdown, exit, server-response delivery, configuration notifications, and other ordinary traffic remain deferred until the active client request completes, preserving the bounded concurrency surface;
6. `$/cancelRequest` remains an out-of-band control message: once its target request frame has been read, the reader can mark that exact pending request generation cancelled immediately before or during worker execution;
7. the original cancellation notification is replayed through the normal server handle surface at a dispatch boundary so lifecycle and trace behavior remain owned by server layers;
8. after every foreground dispatch or request completion, queued notifications are written first, tracked server-to-client requests second, and a direct response last;
9. every outbound object uses the existing UTF-8 `encode_message()` framing primitive and the batch is flushed once;
10. the loop ends when the server reaches `EXITED` or the transport fails.

The server-to-client request queue/pending map is lock-backed because foreground document mutation may trigger refresh/configuration traffic while a request worker is still executing. The notification outbox was already lock-backed. This keeps request IDs unique and preserves terminal retirement semantics under the new bounded concurrency surface.

A cancel frame may arrive after the request frame has been read but before the handler calls `RequestTracker.start()`. The runtime stages a one-shot cancellation only for request ids it has already read and still owns as pending. `start()` consumes that staged state, and dispatch completion clears any unconsumed stage, so an early cancel cannot poison a later reuse of the same JSON-RPC id. Generic cancellation of unknown ids remains harmless.

## Failure and lifecycle behavior

A normal `shutdown` request followed by an `exit` notification returns the exit code chosen by the server lifecycle.

EOF before `exit` is treated as an abnormal transport termination and returns a non-zero process status, including when `shutdown` was received but the required `exit` notification never arrived.

A `FramingError` is also fail-closed and returns non-zero without inventing a JSON-RPC response. Once the byte stream has violated the framing contract, the runtime does not guess where a later frame begins.

The server's terminal notification/request quiescence remains authoritative. After `exit`, queued traffic discarded by the lifecycle layer is not resurrected by the runtime.

## Scope

This milestone keeps exactly one active client-request worker and one foreground snapshot-mutation lane. Document lifecycle changes and workspace-folder scope changes may invalidate the active request through existing exact-generation guards; configuration changes, server-response delivery, lifecycle traffic, and all other ordinary notifications remain serialized behind the worker. It deliberately does not add asyncio, a multi-request worker pool, parallel client requests, general parallel workspace/configuration mutation, concurrent shutdown/exit, socket transports, process supervision, logging to stdout, or editor-specific launch configuration. Any future broader concurrent host must preserve the same exact-snapshot, ordering, cancellation, lifecycle, and outbound-channel invariants with separate deterministic evidence.
