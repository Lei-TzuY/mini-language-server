# Exact-snapshot workspace symbol resolve

The final Nova product negotiates `workspaceSymbol/resolve` when the client advertises `workspace.symbol.resolveSupport.properties` containing `location.range`.

For those clients, `workspace/symbol` returns the symbol name/kind immediately but publishes only the symbol URI in `location`; the exact range is restored lazily by `workspaceSymbol/resolve`. Each symbol carries an opaque resolve token whose record owns the complete workspace semantic snapshot set captured when the symbol result was produced.

Resolve never re-queries the latest workspace. It succeeds only while that exact snapshot set is still current. Same-version replacement, close/reopen, adding or removing an open document, or another workspace-generation change therefore yields `Content modified` rather than mixing generations. Resolve also checkpoints request cancellation immediately before publication.

Clients that do not advertise `location.range` resolve support keep the existing eager `workspaceSymbolProvider: true` behavior and receive complete locations from `workspace/symbol`.
