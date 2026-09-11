# Bounded Nova UInt numeric operators

The Nova product layer now carries exact-snapshot `UInt` knowledge through the numeric operators that the Nova language already implements for the unsigned family.

Supported bounded forms are same-family `UInt` arithmetic with `+`, `-`, `*`, `/`, and `%`, plus same-family equality and ordering. Arithmetic produces `UInt`; equality and ordering produce `Bool`. Operands can be `UInt::MIN`, `UInt::MAX`, exact semantic references with known `UInt` type, or uniquely resolved same-file/cross-file function calls whose result is known as `UInt` through the existing workspace snapshot.

The implementation deliberately remains fail-closed. Unsuffixed integer literals are still `Int`; mixed `Int`/`UInt` operators publish no derived type knowledge, chained comparisons remain unsupported, and unary negation of `UInt` remains unknown because Nova defines unary `-` as `Int`-only. No implicit conversion is invented by the language server.

The numeric result participates in the existing argument, return, explicit-local, assignment, hover/completion/inlay, and unannotated function-result consumers through the same exact semantic/workspace snapshot chain. Cross-file changes, close/reopen, and same-version workspace replacement regressions verify that stale UInt-derived results cannot overwrite newer workspace state.
