# Bounded Nova explicit numeric conversion typing

The Nova product layer recognizes the two implemented checked cross-family conversion forms: `UInt::from(Int)` produces `UInt`, and `Int::from_uint(UInt)` produces `Int`.

Conversion operands reuse the existing exact semantic/workspace expression pipeline, so bounded literals, typed references, same-family arithmetic, `UInt::MIN` / `UInt::MAX`, uniquely resolved function-call results, and nested valid conversions can participate without introducing a parallel type store. The derived result remains bound to the exact parent semantic/workspace snapshots used to type the operand.

The implementation stays fail-closed. Unknown, ambiguous, cyclic, or structurally incomplete operands publish no derived conversion type. Nova still has no implicit Int/UInt conversion and no unsigned literal suffix.

Known-invalid conversions now produce exact-snapshot diagnostics instead of silently collapsing to unknown. `nova.conversion-type` reports a known wrong source family on the exact argument span, while `nova.conversion-arity` reports zero or multiple top-level arguments on the complete conversion invocation. These diagnostics use the same diagnostic snapshot publication path as the rest of the Nova product, so push and negotiated pull diagnostics inherit didChange, close/reopen, cancellation, and stale-result suppression guarantees.

The editor completion surface mirrors only the numeric intrinsics that Nova currently implements. At a trivia-aware `UInt::` member prefix it offers `MIN`, `MAX`, and `from`; at `Int::` it offers `from_uint`. Prefix filtering is deterministic, and completion publication is guarded by both the exact semantic snapshot and the exact workspace snapshot set so same-version replacement, close/reopen, and cancellation cannot publish stale intrinsic items. This is intentionally a bounded Nova product feature rather than a generic member-access subsystem.

Conversion results compose with the existing argument, return, local, assignment, arithmetic/comparison, and unannotated function-result consumers. Workspace rebinding after edits and same-version snapshot replacement continue to use the generic compare-and-commit publication guards.