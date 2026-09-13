# Nova completion insert/replace edits

Nova completion supports LSP `InsertReplaceEdit` when the client advertises
`textDocument.completion.completionItem.insertReplaceSupport`.

For a cursor inside a Nova identifier, the completion item uses:

- an `insert` range from the identifier start to the cursor;
- a `replace` range spanning the complete identifier under the cursor;
- `newText` equal to the normal completion insertion, including negotiated function
  snippets when snippet support is enabled.

This lets a client complete a partially typed name without leaving an identifier suffix
behind. Clients that do not advertise insert/replace support keep the existing completion
shape.

The edit is published only while both the exact semantic snapshot that produced the
cursor query and the captured workspace snapshot set remain current. Same-version
replacement, close/reopen replacement, or other workspace drift must reject the result
with `Content modified` rather than publish an edit against stale source coordinates.
