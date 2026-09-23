# Nova completion-list item defaults

The final Nova completion presentation pipeline can return an LSP `CompletionList`
with a shared `itemDefaults.editRange` when the client explicitly advertises
`textDocument.completion.completionList.itemDefaults` support for `editRange`.

The optimization is negotiated rather than unconditional. Clients that do not advertise
the property continue to receive the existing complete `CompletionItem[]` result. Clients
that advertise other item-default properties but not `editRange` also keep the existing
shape.

For one completion request, every item is produced for the same cursor and identifier
replacement region. The presentation pipeline therefore computes that source region once
from the exact current semantic snapshot and publishes it as a list default:

- clients with `completionItem.insertReplaceSupport` receive shared `insert` and
  `replace` ranges;
- other clients receive one ordinary replacement `Range`;
- plain items rely on their label as the edit text;
- items whose insertion differs from the label, including Nova function and numeric
  intrinsic snippets, use `CompletionItem.textEditText`;
- per-item `insertTextFormat` remains attached to snippet items, so the list default does
  not falsely classify plain identifiers as snippets.

The shared range is rendered through the already negotiated session position encoding.
UTF-8, UTF-16, and UTF-32 clients therefore receive the same source region measured in
their own LSP character units.

The feature composes with exact-snapshot completion resolve. Opaque resolve data is still
captured before presentation, and resolve may add only the negotiated lazy properties;
the client-returned `textEditText` remains unchanged. Prefix filtering, item
classification, snippets, numeric intrinsic completion, conversion-operand completion,
cancellation, and same-version workspace replacement continue through the existing
single completion publication boundary.
