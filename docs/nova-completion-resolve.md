# Nova completion item resolve

The final Nova product negotiates `completionItem/resolve` only when the client advertises `textDocument.completion.completionItem.resolveSupport` for `documentation`. In that mode, completion responses carry opaque resolve data and `completionProvider.resolveProvider` is advertised as `true`.

Resolve is not a label-only lookup. Every opaque token retains the exact semantic snapshot and exact workspace snapshot set that produced the completion item. Resolution succeeds only while those identities remain current; same-version semantic replacement, workspace replacement, document changes, close/reopen, or other stale lineage returns LSP `Content modified` instead of attaching documentation to an obsolete item. Cancellation is checked again at the final publication boundary.

Resolved function items expose their exact bounded Nova signature as fenced Nova documentation. Typed parameter, local, and constant items expose the bounded type already proven by the Nova product layer. The generic language-independent stores and query layer remain unchanged; this is product-level protocol composition over the existing exact-snapshot completion surface.
