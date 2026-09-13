# Nova function completion snippets

The final Nova product extends exact-snapshot function completion with call snippets when the client advertises `textDocument.completion.completionItem.snippetSupport`.

For a uniquely resolved function, the completion item keeps its existing label and exact signature detail while adding a snippet derived from that exact signature. For example, `fn render(width: Int, height: Int) -> Unit` inserts `render(${1:width}, ${2:height})`; a zero-parameter function inserts `name()`.

Clients without snippet support keep the existing plain completion shape. Snippet synthesis also composes with `completionItem/resolve`: when `detail` is deferred, the snippet is still derived from the exact captured completion record rather than recomputed from a newer workspace state.

Publication remains guarded by the exact workspace snapshot set that produced the completion response. Same-version replacement, close/reopen replacement, or other workspace invalidation therefore rejects the stale result instead of publishing a snippet from obsolete function metadata.
