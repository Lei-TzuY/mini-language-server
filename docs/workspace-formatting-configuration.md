# Workspace formatting configuration

The final Nova product obtains save-time formatting settings through the standard LSP `workspace/configuration` request when the client advertises `workspace.configuration = true`.

## Configuration scopes

Each request contains one global fallback item followed by one item for every active workspace folder. Folder items use the standard `scopeUri` field:

```json
{
  "items": [
    {"section":"mini-language-server.formatting"},
    {"section":"mini-language-server.formatting","scopeUri":"file:///workspace/app"},
    {"section":"mini-language-server.formatting","scopeUri":"file:///workspace/lib"}
  ]
}
```

The global item is always first. Folder items are ordered deterministically by URI. In legacy sessions with no configured workspace-folder scope, the request therefore remains the historical single global item.

The bounded section accepts:

- `tabSize`: a positive integer
- `insertSpaces`: a boolean

A `null` item resets that scope to the built-in default of four spaces. Missing properties use the same defaults. An invalid item preserves the last valid value for that scope; a newly introduced invalid scope starts from the default. A client error preserves the complete last valid configuration cache.

## Save-time lookup

`textDocument/willSaveWaitUntil` selects formatting settings from the most specific workspace folder containing the document URI. Nested folders can therefore override a parent folder. An open document outside every configured folder uses the global fallback. Sessions without workspace-folder scoping also use the global fallback.

Manual document/range/on-type formatting continues to use the per-request LSP `FormattingOptions` supplied by the editor; workspace configuration only controls save-time formatting.

## Generation and folder lifecycle

Formatting configuration has its own generation identity. The first request is queued after the client sends `initialized`, so the server does not issue client requests during the initialize handshake. When the client also advertises `workspace.didChangeConfiguration.dynamicRegistration = true`, the server first sends one tracked `client/registerCapability` request for `workspace/didChangeConfiguration` with `registerOptions.section = "mini-language-server.formatting"`. The registration ID is stable for the session, registration is attempted at most once, and clients without dynamic-registration support keep the legacy configuration-only flow.

`workspace/didChangeConfiguration` never trusts the notification's arbitrary settings payload. Whether the notification arrives through dynamic registration or legacy client behavior, it advances the configuration generation and requests the standard section again. Registration success or failure never mutates the formatting cache; only a validated `workspace/configuration` response can do that.

Workspace-folder membership changes do the same. Adding or removing a folder changes the requested `scopeUri` set, advances the generation, and invalidates any pending response for the previous scope set. The old response is retired but never applied; once it arrives, one request for the newest generation is queued. Removing a folder changes URI lookup immediately, so documents from that folder fall back to the last valid global setting while the refreshed configuration is pending.

A valid response is applied only when both its generation and its captured ordered scope list still correspond to the current request generation. This prevents a response for an old folder universe from installing settings into a newer workspace scope.

Clients that do not advertise `workspace.configuration` receive no configuration request and keep deterministic four-space save formatting.

The generic server-to-client request substrate validates and retires the JSON-RPC response before exposing its payload to this consumer. Unknown IDs, mixed result/error responses, and malformed error responses never reach configuration logic.
