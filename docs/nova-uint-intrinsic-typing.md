# Bounded Nova UInt intrinsic typing

The Nova product layer recognizes the implemented `UInt::MIN` and `UInt::MAX` constant forms as exact-snapshot `UInt` values. This is intentionally narrower than a full UInt frontend: there is still no unsigned literal suffix, and UInt arithmetic, ordering, equality, and conversion intrinsic typing remain separate slices.

The bounded UInt knowledge participates in argument validation, return validation, explicit local and assignment validation, unannotated function-result inference, and uniquely resolved same-file/cross-file explicit function result propagation. A declared `-> UInt` function is treated as value-returning and therefore requires a proven value return or a known tail expression.

The implementation remains in Nova product composition. Generic document, syntax, symbol, semantic, diagnostic, workspace, cancellation, and publication stores are unchanged. Results continue to depend on the exact semantic/workspace snapshot, with regressions covering `didChange`, close/reopen, and same-version workspace replacement.
