# LSP position encoding negotiation

The language-independent server negotiates one position encoding for the complete LSP session. This keeps incoming positions, incremental edit ranges, outgoing ranges, and semantic-token offsets on one coordinate system instead of allowing individual features to interpret characters differently.

During `initialize`, the server reads `capabilities.general.positionEncodings` and selects the first encoding it implements. The implemented encodings are `utf-8` and `utf-16`. When the client omits the capability or advertises no implemented encoding, the server preserves the LSP-compatible `utf-16` default. The selected value is returned as `ServerCapabilities.positionEncoding`.

The negotiated encoding is immutable once documents are open. `DocumentStore` uses it when applying ranged `textDocument/didChange` notifications, so a UTF-8 client addresses an astral code point by its encoded byte width while a UTF-16 client addresses the same code point by its surrogate-pair width. Positions that split a UTF-8 code point or UTF-16 surrogate pair are rejected without committing a partial document update.

`SourceText` remains the single conversion primitive from Python string offsets to LSP line/character positions. Product and protocol features obtain source views through the server's session-aware factory. Diagnostics, navigation, rename/workspace edits, hover, completion-related ranges, formatting, selection ranges, inlay hints, folding, CodeLens/call hierarchy locations, and other range-bearing responses therefore share the negotiated units.

Semantic tokens use the same session encoding for both token starts and token lengths, including Nova's product-specific numeric intrinsic tokens and semantic-token modifiers. Range-scoped semantic-token requests are decoded with the same encoding before filtering.

Negotiation does not weaken the repository's exact-snapshot guarantees. Position encoding is fixed for the session; document, syntax, symbol, semantic, diagnostic, and workspace generation identity still determines whether a result is current. Cancellation and stale-result suppression continue to run at the same publication boundaries.
