# Bounded LSP framing headers

The binary LSP framing layer treats the complete header section as bounded transport input rather than trusting peers to eventually send an empty line.

## Limits

`MessageReader` applies three independent framing bounds before JSON decoding:

- one header line is limited to 8192 bytes;
- the complete header section, including the terminating empty line, is limited to 64 KiB by default;
- at most 64 header fields are accepted by default;
- the payload remains independently limited to 8 MiB by default.

The total-byte and field-count budgets are configurable per reader for deterministic tests or tighter embedding environments. Non-positive budgets are rejected at construction.

## Bounded stream reads

The parser does not call unbounded `readline()` and then validate the resulting allocation. Before every header read it computes the remaining section budget and requests at most:

`min(header-line-limit, remaining-header-budget) + 1`

bytes from the stream.

The extra byte is only a detection sentinel. If it is returned, the parser can prove that either the current line or the remaining total header budget has been exceeded and fails before accumulating or decoding an unbounded header value.

## Fail-fast behavior

Header byte/count limits are checked before ASCII decoding and before insertion into the header dictionary. Oversized or over-count frames therefore never proceed to payload `read()`.

The existing framing invariants remain unchanged:

- clean EOF before any frame returns `None`;
- EOF after a frame has started is a framing error;
- duplicate/missing header names and missing/invalid `Content-Length` are rejected;
- payload length is checked before payload read;
- truncated payloads, invalid UTF-8, invalid JSON, and non-object JSON-RPC payloads are rejected.

This keeps the transport substrate bounded independently of language-server feature routing.
