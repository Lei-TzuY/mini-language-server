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

The runtime keeps ordinary server dispatch deliberately serialized while separating framing from dispatch:

1. one background reader decodes bounded `Content-Length` frames with `MessageReader`;
2. ordinary requests, responses, and notifications enter one FIFO dispatch queue;
3. the calling thread dispatches those queued objects through `LanguageServer.handle()` one at a time;
4. `$/cancelRequest` is the only control message allowed to cross that queue boundary: once its target request frame has been read, the reader can mark that exact pending request generation cancelled immediately, even while the foreground dispatcher is still inside the request handler;
5. the original cancellation notification is replayed through the normal server handle surface at the dispatch boundary so lifecycle and trace behavior are preserved;
6. after each ordinary dispatch, queued notifications are written first, tracked server-to-client requests second, and the direct response last;
7. every outbound object uses the existing UTF-8 `encode_message()` framing primitive and the batch is flushed once;
8. the loop ends when the server reaches `EXITED` or the transport fails.

This is not a worker pool. Document changes, workspace mutations, shutdown, server-response delivery, and ordinary client requests remain single-dispatch ordered. The reader thread exists specifically so an executing request can observe cancellation at its next existing `RequestTracker.checkpoint()` instead of waiting for that request to finish before stdin is read again.

A cancel frame may arrive after the request frame has been read but before the handler calls `RequestTracker.start()`. The runtime stages a one-shot cancellation only for request ids it has already read and still owns as pending. `start()` consumes that staged state, and dispatch completion clears any unconsumed stage, so an early cancel cannot poison a later reuse of the same JSON-RPC id. Generic cancellation of unknown ids remains harmless.

## Failure and lifecycle behavior

A normal `shutdown` request followed by an `exit` notification returns the exit code chosen by the server lifecycle.

EOF before `exit` is treated as an abnormal transport termination and returns a non-zero process status, including when `shutdown` was received but the required `exit` notification never arrived.

A `FramingError` is also fail-closed and returns non-zero without inventing a JSON-RPC response. Once the byte stream has violated the framing contract, the runtime does not guess where a later frame begins.

The server's terminal notification/request quiescence remains authoritative. After `exit`, queued traffic discarded by the lifecycle layer is not resurrected by the runtime.

## Scope

This milestone keeps the executable server single-dispatch while adding one framing reader thread for live request cancellation. It deliberately does not add asyncio, a request worker pool, parallel document/workspace mutation, socket transports, process supervision, logging to stdout, or editor-specific launch configuration. Any future broader concurrent host must preserve the same ordering and lifecycle ownership invariants with separate deterministic evidence.
