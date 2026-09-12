# Nova completion item resolve

The final Nova product negotiates `completionItem/resolve` only for properties the client explicitly advertises through `textDocument.completion.completionItem.resolveSupport`. The product currently supports lazy `detail` and `documentation` resolution; `completionProvider.resolveProvider` is advertised only when at least one of those properties is requested.

When `detail` is supported, the initial completion response intentionally omits that field while preserving the full completion item behind opaque resolve data. `completionItem/resolve` restores the exact original detail from the captured item rather than recomputing it from the latest workspace. When `documentation` is supported, resolve derives documentation from that same captured item. Clients may negotiate either property independently or both together.

Resolve is not a label-only lookup. Every opaque token retains the exact semantic snapshot and exact workspace snapshot set that produced the completion item. Resolution succeeds only while those identities remain current; same-version semantic replacement, workspace replacement, document changes, close/reopen, or other stale lineage returns LSP `Content modified` instead of enriching an obsolete item. Cancellation is checked again at the final publication boundary.

Resolved function items expose their exact bounded Nova signature. Typed parameter, local, and constant items preserve the bounded type already proven by the Nova product layer. The generic language-independent stores and query layer remain unchanged; this is product-level protocol composition over the existing exact-snapshot completion surface.
