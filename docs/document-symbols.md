# Negotiated Nova document symbols

The Nova product implements `textDocument/documentSymbol` against the exact current semantic snapshot and now honors the LSP client's `hierarchicalDocumentSymbolSupport` capability.

## Hierarchical clients

When `textDocument.documentSymbol.hierarchicalDocumentSymbolSupport = true`, the server returns `DocumentSymbol[]`.

- each Nova function declaration is a root symbol;
- parameters and locals whose `NovaFunctionSyntax.owner` points at that function become deterministic children;
- the function `range` spans from the `fn` keyword through the matching closing brace when that bounded structure is available;
- the function `selectionRange` remains the exact function-name span;
- parameter/local `range` and `selectionRange` remain their exact declaration spans;
- the implementation does not invent block-level local nesting because the current Nova syntax model guarantees function ownership, not a full lexical block-symbol tree.

## Flat clients

Clients that support document symbols but do not advertise hierarchical support receive standard `SymbolInformation[]` rather than flattened `DocumentSymbol` objects.

Each item carries:

- `name`;
- the existing deterministic LSP `SymbolKind`;
- `location.uri` and `location.range`;
- `containerName` for Nova parameters and locals owned by a function.

This preserves a standards-compliant flat result without discarding the function ownership already present in the syntax snapshot.

## Exactness and coordinates

Both result forms are rendered through the session-aware source view, so negotiated UTF-8 and UTF-16 positions use the same coordinate contract as the rest of the server. The request remains bound to the exact current `SemanticSnapshot`; same-version semantic replacement, document replacement, stale requests, and cancellation retain the existing fail-closed behavior.
