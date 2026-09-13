# Nova completion classification and ranking

The final Nova product enriches exact-snapshot completion items with standard LSP `CompletionItemKind` values and deterministic `sortText` ordering.

The shipped classification is intentionally bounded to semantic roles already proven by the completion pipeline:

- parameters and locals use `CompletionItemKind.Variable` (`6`) and rank first;
- constants use `CompletionItemKind.Constant` (`21`) and rank after lexical values;
- functions use `CompletionItemKind.Function` (`3`) and rank after values and constants.

Within a rank, `sortText` keeps the completion label as the deterministic secondary key. Unknown completion shapes are left untouched rather than guessed.

This layer composes after the existing exact-snapshot Nova completion surface, including function snippets, lazy completion resolve, numeric intrinsic members, and negotiated `InsertReplaceEdit`. It does not recompute symbols or types and it does not weaken freshness: stale same-version/workspace replacements continue to fail at the inherited exact semantic/workspace publication gates before classification is published.

The generic language-independent stores and query layer remain unchanged; completion role classification is Nova product presentation over already-derived exact semantic data.
