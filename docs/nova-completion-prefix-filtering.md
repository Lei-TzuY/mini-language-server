# Nova completion prefix filtering

Nova completion publication is filtered by the ASCII identifier prefix immediately before the request cursor. The filter is a product-layer behavior: the generic symbol, semantic, and workspace stores remain language-independent.

The prefix is read from the same exact semantic document snapshot that produced the candidate set. Same-file lexical candidates and uniquely exposed cross-file function candidates must start with that prefix. An empty prefix preserves the existing complete candidate set, so completion requests made after whitespace or punctuation retain discovery behavior.

Filtering composes after the existing Nova completion pipeline, including lexical-scope visibility, typed details, function snippets, insert/replace edits, standard completion kinds, deterministic `sortText`, and completion resolve data. Publication is guarded again against both the exact semantic snapshot and the exact workspace snapshot set. A same-version replacement, close/reopen race, cancellation, or other stale state must therefore reject the response rather than publish candidates derived from obsolete source coordinates.
