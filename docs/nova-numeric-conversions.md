# Bounded Nova explicit numeric conversion typing

The Nova product layer recognizes the two implemented checked cross-family conversion forms: `UInt::from(Int)` produces `UInt`, and `Int::from_uint(UInt)` produces `Int`.

Conversion operands reuse the existing exact semantic/workspace expression pipeline, so bounded literals, typed references, same-family arithmetic, `UInt::MIN` / `UInt::MAX`, uniquely resolved function-call results, and nested valid conversions can participate without introducing a parallel type store. The derived result remains bound to the exact parent semantic/workspace snapshots used to type the operand.

The implementation stays fail-closed. Wrong-family operands, multiple arguments, malformed parentheses, ambiguous or cyclic function results, and unknown expressions publish no derived conversion type. Nova still has no implicit Int/UInt conversion and no unsigned literal suffix.

Conversion results compose with the existing argument, return, local, assignment, arithmetic/comparison, and unannotated function-result consumers. Workspace rebinding after edits and same-version snapshot replacement continue to use the generic compare-and-commit publication guards.
