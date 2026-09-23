# Nova multi-range formatting

The Nova product implements the LSP 3.18 `textDocument/rangesFormatting`
request when the client advertises
`textDocument.rangeFormatting.rangesSupport = true`.

Capability negotiation remains backward compatible. Clients that support only the
original single-range request continue to receive
`documentRangeFormattingProvider: true`. Clients that advertise multi-range
support receive `documentRangeFormattingProvider: { "rangesSupport": true }`.

Single-range and multi-range requests share one trivia-aware indentation engine.
The engine computes structural brace depth from the complete masked document once
and emits a leading-whitespace edit only when that exact edit span is fully
contained in at least one requested range. Multiple or overlapping requested
ranges therefore cannot duplicate the same edit, and source outside the union of
eligible ranges is never modified.

A multi-range request owns one request lifecycle and one semantic snapshot. Every
range is validated in the negotiated LSP position encoding before formatting
begins. If any range is malformed or outside the document, the complete request
fails with Invalid params and no partial edit list is published. Cancellation is
checked while the range set is validated and before publication. Same-version
semantic replacement or other exact-snapshot drift rejects the complete result
with Content modified.

Formatting options are identical to the existing range formatter:
`tabSize` must be a positive integer and `insertSpaces` must be Boolean.
An empty range list is a valid no-op that returns an empty edit list. UTF-8,
UTF-16, and UTF-32 positions all pass through the existing session coordinate
contract.
