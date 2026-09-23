# LSP trace observability

The final Nova product implements the standard LSP trace control notification `$/setTrace` and emits `$/logTrace` notifications from one language-independent outer wrapper.

## Trace modes

The accepted values are the standard LSP modes:

- `off`: emit no trace notifications.
- `messages`: emit deterministic lifecycle messages for inbound client requests, notifications, and responses plus outbound server responses, notifications, and tracked server-to-client requests.
- `verbose`: emit the same lifecycle messages plus a deterministic structural summary.

An invalid `$/setTrace` payload is ignored because the request is a notification and has no response channel. Changing the trace mode does not mutate document, semantic, workspace, diagnostic, or request-tracker state.

## Product-wide coverage

Tracing wraps the final composed product rather than one intermediate handler class. Requests handled by later product layers such as workspace symbols, diagnostics, semantic tokens, formatting, CodeLens, and completion therefore share the same trace lifecycle.

Outbound server notifications are centralized through the generic notification queue. Push diagnostics and standard `$/progress` values therefore participate automatically. Tracked server-to-client requests, including configuration/registration and workspace refresh requests, are traced when queued; their client responses are traced when they re-enter the normal `handle` path.

The trace notification itself bypasses the generic notification hook so `$/logTrace` cannot recursively trace another `$/logTrace`.

## Privacy and determinism

Verbose tracing intentionally records structure, not source payloads. It includes method/request identifiers plus object keys, value kinds, and string/array lengths. It does not serialize document text, URIs, diagnostic messages, configuration values, or arbitrary request/result contents.

This keeps tests deterministic and makes tracing useful for lifecycle debugging without creating a second copy of user source in the trace stream.

## Error and cancellation visibility

Outbound error responses are traced with their JSON-RPC/LSP error code. Existing cancellation and stale-result behavior is unchanged; when a request returns `Request cancelled` or `Content modified`, the trace observes that actual response instead of inventing a separate completion state.

Tracing is observational only. It does not weaken exact-snapshot compare-and-commit guards, change request ordering, consume cancellation, or bypass any publication boundary.
