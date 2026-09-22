# Workspace formatting configuration

The final Nova product can obtain save-time formatting settings through the standard LSP `workspace/configuration` request when the client advertises `workspace.configuration = true`.

The server requests one configuration item:

```json
{"section":"mini-language-server.formatting"}
```

The bounded section accepts:

- `tabSize`: a positive integer
- `insertSpaces`: a boolean

Missing properties use the built-in defaults of four spaces. A `null` configuration item resets both values to those defaults. Invalid response shapes or invalid property values do not replace the last valid configuration.

The request is first queued after the client sends `initialized`, so the server does not issue client requests during the initialize handshake. `workspace/didChangeConfiguration` does not trust or parse the notification's arbitrary settings payload; it invalidates the cached generation and requests the standard configuration section again.

Formatting configuration has its own generation identity. If a configuration change arrives while an older `workspace/configuration` request is pending, the old response is retired but not applied. The server immediately queues one request for the newest generation. A client error response leaves the last valid formatting settings intact; a later configuration-change notification can request again.

The cached values are consumed by `textDocument/willSaveWaitUntil`. Manual document/range/on-type formatting continues to use the per-request LSP FormattingOptions supplied by the editor. This removes the previous hard-coded four-space save behavior while preserving the exact document/semantic snapshot guards around the returned save edit.

Clients that do not advertise `workspace.configuration` receive no configuration request and keep the existing four-space save-formatting default.

The generic server-to-client request substrate validates and retires the JSON-RPC response before exposing the payload to a consumer hook. Unknown IDs, mixed result/error responses, and malformed error responses never reach configuration logic.
