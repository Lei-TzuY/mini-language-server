"""Pure fail-closed constant value proofs for bounded Nova expressions."""

from __future__ import annotations

_COMPARISONS = ("==", "!=", "<=", ">=", "<", ">")
_UNARY_PLUS_PRECEDERS = frozenset("({[=,:;!+-*/%&|<>")


def bounded_integer_constant_value(expression: str) -> int | None:
    parsed = _integer_expression(expression, 0)
    if parsed is None:
        return None
    value, cursor = parsed
    return value if _skip_ws(expression, cursor) == len(expression) else None


def bounded_boolean_constant_value(expression: str) -> bool | None:
    value = _unwrap(expression)
    if not value:
        return None
    if value == "true":
        return True
    if value == "false":
        return False

    for operator, reducer in (("||", any), ("&&", all)):
        parts = _split_logical(value, operator)
        if parts is None:
            continue
        if not parts:
            return None
        constants = [bounded_boolean_constant_value(part) for part in parts]
        if any(item is None for item in constants):
            return None
        return reducer(constants)

    comparisons = _top_level_comparisons(value)
    if comparisons:
        if len(comparisons) != 1:
            return None
        operator, offset = comparisons[0]
        left = value[:offset]
        right = value[offset + len(operator) :]
        left_int = _condition_integer(left)
        right_int = _condition_integer(right)
        if left_int is not None and right_int is not None:
            return {
                "==": left_int == right_int,
                "!=": left_int != right_int,
                "<": left_int < right_int,
                "<=": left_int <= right_int,
                ">": left_int > right_int,
                ">=": left_int >= right_int,
            }[operator]
        if operator not in {"==", "!="}:
            return None
        left_bool = bounded_boolean_constant_value(left)
        right_bool = bounded_boolean_constant_value(right)
        if left_bool is None or right_bool is None:
            return None
        return left_bool == right_bool if operator == "==" else left_bool != right_bool

    if value.startswith("!") and not value.startswith("!="):
        operand = bounded_boolean_constant_value(value[1:])
        return None if operand is None else not operand
    return None


def _condition_integer(expression: str) -> int | None:
    for offset, character in enumerate(expression):
        if character != "+":
            continue
        cursor = offset - 1
        while cursor >= 0 and expression[cursor].isspace():
            cursor -= 1
        if cursor < 0 or expression[cursor] in _UNARY_PLUS_PRECEDERS:
            return None
    return bounded_integer_constant_value(expression)


def _split_logical(expression: str, operator: str) -> tuple[str, ...] | None:
    depth = 0
    offsets: list[int] = []
    cursor = 0
    while cursor < len(expression):
        character = expression[cursor]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth < 0:
                return None
        elif depth == 0 and expression.startswith(operator, cursor):
            offsets.append(cursor)
            cursor += len(operator) - 1
        cursor += 1
    if depth != 0 or not offsets:
        return None
    result: list[str] = []
    start = 0
    for offset in (*offsets, len(expression)):
        part = expression[start:offset].strip()
        if not part:
            return ()
        result.append(part)
        start = offset + len(operator)
    return tuple(result)


def _top_level_comparisons(expression: str) -> tuple[tuple[str, int], ...]:
    depth = 0
    result: list[tuple[str, int]] = []
    cursor = 0
    while cursor < len(expression):
        character = expression[cursor]
        if character == "(":
            depth += 1
            cursor += 1
            continue
        if character == ")":
            depth -= 1
            if depth < 0:
                return ()
            cursor += 1
            continue
        if depth:
            cursor += 1
            continue
        operator = next(
            (token for token in _COMPARISONS if expression.startswith(token, cursor)),
            None,
        )
        if operator is None:
            cursor += 1
            continue
        result.append((operator, cursor))
        cursor += len(operator)
    return () if depth else tuple(result)


def _unwrap(expression: str) -> str:
    value = expression.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        closes_at_end = False
        for index, character in enumerate(value):
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < 0:
                    return value
                if depth == 0:
                    closes_at_end = index == len(value) - 1
                    break
        if not closes_at_end:
            return value
        value = value[1:-1].strip()
    return value


def _integer_expression(expression: str, offset: int) -> tuple[int, int] | None:
    parsed = _integer_term(expression, offset)
    if parsed is None:
        return None
    left, cursor = parsed
    while True:
        operator_at = _skip_ws(expression, cursor)
        if operator_at >= len(expression) or expression[operator_at] not in "+-":
            return left, cursor
        operator = expression[operator_at]
        parsed = _integer_term(expression, operator_at + 1)
        if parsed is None:
            return None
        right, cursor = parsed
        left = left + right if operator == "+" else left - right


def _integer_term(expression: str, offset: int) -> tuple[int, int] | None:
    parsed = _integer_primary(expression, offset)
    if parsed is None:
        return None
    left, cursor = parsed
    while True:
        operator_at = _skip_ws(expression, cursor)
        if operator_at >= len(expression) or expression[operator_at] not in "*/%":
            return left, cursor
        operator = expression[operator_at]
        parsed = _integer_primary(expression, operator_at + 1)
        if parsed is None:
            return None
        right, cursor = parsed
        if operator in "/%" and right == 0:
            return None
        if operator == "*":
            left *= right
        else:
            quotient = abs(left) // abs(right)
            quotient = -quotient if (left < 0) != (right < 0) else quotient
            left = quotient if operator == "/" else left - quotient * right


def _integer_primary(expression: str, offset: int) -> tuple[int, int] | None:
    cursor = _skip_ws(expression, offset)
    sign = 1
    signed = False
    if cursor < len(expression) and expression[cursor] in "+-":
        signed = True
        sign = -1 if expression[cursor] == "-" else 1
        cursor = _skip_ws(expression, cursor + 1)
    if cursor < len(expression) and expression[cursor].isdigit():
        start = cursor
        while cursor < len(expression) and expression[cursor].isdigit():
            cursor += 1
        if cursor < len(expression) and (
            expression[cursor].isalnum() or expression[cursor] in "_."
        ):
            return None
        return sign * int(expression[start:cursor]), cursor
    if signed or cursor >= len(expression) or expression[cursor] != "(":
        return None
    parsed = _integer_expression(expression, cursor + 1)
    if parsed is None:
        return None
    value, cursor = parsed
    cursor = _skip_ws(expression, cursor)
    if cursor >= len(expression) or expression[cursor] != ")":
        return None
    return value, cursor + 1


def _skip_ws(expression: str, offset: int) -> int:
    while offset < len(expression) and expression[offset].isspace():
        offset += 1
    return offset
