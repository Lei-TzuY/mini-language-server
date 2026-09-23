# Synchronous stdio runtime

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

The runtime is intentionally synchronous:

1. read one bounded `Content-Length` frame with `MessageReader`;
2. dispatch the decoded JSON-RPC object through `LanguageServer.handle()`;
3. drain the server's outbound channels;
4. encode every outbound object with the existing UTF-8 `encode_message()` framing primitive;
5. flush the output stream;
6. repeat until the server reaches `EXITED`.

One inbound dispatch produces one deterministic outbound batch. Queued notifications are written first, tracked server-to-client requests second, and the direct response last. This keeps progress, cancellation, diagnostic, and trace notifications produced during request handling ahead of that request's terminal response.

The runtime does not create a background scheduler. Existing exact-snapshot, cancellation, stale-result, server-request, notification-quiescence, and shutdown/exit semantics remain owned by the server layers.

## Failure and lifecycle behavior

A normal `shutdown` request followed by an `exit` notification returns the exit code chosen by the server lifecycle.

EOF before `exit` is treated as an abnormal transport termination and returns a non-zero process status, including when `shutdown` was received but the required `exit` notification never arrived.

A `FramingError` is also fail-closed and returns non-zero without inventing a JSON-RPC response. Once the byte stream has violated the framing contract, the runtime does not guess where a later frame begins.

The server's terminal notification/request quiescence remains authoritative. After `exit`, queued traffic discarded by the lifecycle layer is not resurrected by the runtime.

## Scope

This milestone makes the existing language server executable over stdio. It deliberately does not add asyncio, worker threads, socket transports, process supervision, logging to stdout, or editor-specific launch configuration. Any future concurrent host must preserve the same ordering and lifecycle ownership invariants with separate deterministic evidence.
