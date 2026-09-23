# Nova debugger inline values

The final Nova product supports the standard LSP `textDocument/inlineValue` request as a debugger-oriented integration surface. The language server does not invent runtime values. Instead, it returns `InlineValueVariableLookup` records that identify lexically visible Nova parameters and locals so the debugger adapter can look up the corresponding runtime variable in the requested frame.

Clients opt in through `textDocument.inlineValue`. Supporting sessions advertise `inlineValueProvider: true`. Requests must provide a valid viewport `range` plus an `InlineValueContext` containing an integer `frameId` and a valid `stoppedLocation`.

The stopped location selects one exact Nova function body through the same function-owner helper used by lexical completion. Visibility mirrors the current Nova resolver:

- parameters belong to their exact function owner;
- a local becomes visible only after its declaration;
- exactly one preceding local with a given name shadows a parameter of the same name;
- multiple preceding locals with the same name are ambiguous and fail closed;
- names from other functions never enter the result.

For each visible target, the server returns declaration/reference occurrences fully contained in the requested viewport. Each item carries the exact negotiated LSP range, `variableName`, and `caseSensitiveLookup: true`. Results are deterministic and source ordered.

Publication is bound to the exact semantic snapshot that supplied the symbol identities. A document mutation before commit returns `Content modified`; request cancellation returns `Request cancelled`; neither path publishes stale inline values or leaves request-tracker residue. UTF-8, UTF-16, and UTF-32 clients reuse the session-wide position encoding contract.

This capability intentionally exposes variable lookup identities rather than static type strings or guessed values. Runtime evaluation remains owned by the debugger, while the language server owns only lexical visibility and source identity.
