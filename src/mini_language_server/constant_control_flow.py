"""Product-independent bounded control-flow facts for Nova source."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .constant_values import bounded_boolean_constant_value

_IF_BRANCH = re.compile(r"\bif\b(?P<condition>[^{};]*)\{")
_ELSE_IF_BRANCH = re.compile(r"\s*else\s+if\b(?P<condition>[^{};]*)\{")
_ELSE_BRANCH = re.compile(r"\s*else\s*\{")
_CONTROL_FLOW = re.compile(r"\b(?P<kind>if|while)\b")
_BREAK = re.compile(r"\bbreak\b")


@dataclass(frozen=True, slots=True)
class ControlFlowStatement:
    """One structurally complete bounded if/while statement."""

    kind: str
    start: int
    condition_start: int
    condition_end: int
    body_open: int
    body_close: int

    @property
    def end(self) -> int:
        return self.body_close + 1


def control_flow_statements(code: str) -> tuple[ControlFlowStatement, ...]:
    """Return complete control-flow statements from a trivia-masked Nova view."""
    result: list[ControlFlowStatement] = []
    for match in _CONTROL_FLOW.finditer(code):
        statement = _control_flow_statement_from_match(code, match)
        if statement is not None:
            result.append(statement)
    return tuple(result)


def control_flow_statement_at(
    code: str, offset: int, *, kind: str | None = None
) -> ControlFlowStatement | None:
    """Parse one complete control-flow statement starting exactly at offset."""
    match = _CONTROL_FLOW.match(code, offset)
    if match is None or (kind is not None and match.group("kind") != kind):
        return None
    return _control_flow_statement_from_match(code, match)


def _control_flow_statement_from_match(
    code: str, match: re.Match[str]
) -> ControlFlowStatement | None:
    cursor = _next_non_space(code, match.end())
    if cursor is None:
        return None

    if code[cursor] == "(":
        condition_close = _matching_delimiter(code, cursor, "(", ")")
        if condition_close is None:
            return None
        condition_start = cursor + 1
        condition_end = condition_close
        body_open = _next_non_space(code, condition_close + 1)
    else:
        condition_start = cursor
        body_open = _bare_condition_body_open(code, cursor)
        condition_end = body_open if body_open is not None else cursor

    if body_open is None or code[body_open] != "{":
        return None
    body_close = _matching_brace(code, body_open)
    if body_close is None:
        return None

    while condition_start < condition_end and code[condition_start].isspace():
        condition_start += 1
    while condition_end > condition_start and code[condition_end - 1].isspace():
        condition_end -= 1
    if condition_start >= condition_end:
        return None

    return ControlFlowStatement(
        kind=match.group("kind"),
        start=match.start(),
        condition_start=condition_start,
        condition_end=condition_end,
        body_open=body_open,
        body_close=body_close,
    )


def _bare_condition_body_open(code: str, offset: int) -> int | None:
    paren_depth = 0
    bracket_depth = 0
    for cursor in range(offset, len(code)):
        character = code[cursor]
        if character == "(":
            paren_depth += 1
        elif character == ")":
            paren_depth -= 1
            if paren_depth < 0:
                return None
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return None
        elif character == "{" and paren_depth == 0 and bracket_depth == 0:
            return cursor
        elif character in ";}" and paren_depth == 0 and bracket_depth == 0:
            return None
    return None


def proven_dead_branch_spans(code: str) -> tuple[tuple[int, int], ...]:
    """Return branch-body spans that bounded constant conditions prove unreachable."""
    spans: list[tuple[int, int]] = []

    for match in _IF_BRANCH.finditer(code):
        if _is_else_if(code, match.start()):
            continue

        branch_open = match.end() - 1
        branch_close = _matching_brace(code, branch_open)
        if branch_close is None:
            continue

        constant = bounded_boolean_constant_value(match.group("condition"))
        if constant is False:
            spans.append((branch_open + 1, branch_close))
        prior_true = constant is True
        cursor = branch_close + 1

        while cursor < len(code):
            remainder = code[cursor:]
            else_if = _ELSE_IF_BRANCH.match(remainder)
            if else_if is not None:
                branch_open = cursor + else_if.end() - 1
                branch_close = _matching_brace(code, branch_open)
                if branch_close is None:
                    break

                constant = bounded_boolean_constant_value(
                    else_if.group("condition")
                )
                if prior_true or constant is False:
                    spans.append((branch_open + 1, branch_close))
                if constant is True:
                    prior_true = True
                cursor = branch_close + 1
                continue

            otherwise = _ELSE_BRANCH.match(remainder)
            if otherwise is None:
                break
            branch_open = cursor + otherwise.end() - 1
            branch_close = _matching_brace(code, branch_open)
            if branch_close is None:
                break
            if prior_true:
                spans.append((branch_open + 1, branch_close))
            break

    spans.sort()
    return tuple(spans)


def proven_non_fallthrough_while_spans(
    code: str,
) -> tuple[tuple[int, int], ...]:
    """Return while-statement spans proven unable to fall through."""
    spans: list[tuple[int, int]] = []
    for statement in control_flow_statements(code):
        if statement.kind != "while":
            continue
        if (
            bounded_boolean_constant_value(
                code[statement.condition_start : statement.condition_end]
            )
            is not True
        ):
            continue

        body = code[statement.body_open + 1 : statement.body_close]
        if _has_reachable_break_targeting_current_loop(body):
            continue
        spans.append((statement.start, statement.end))

    return tuple(spans)


def _has_reachable_break_targeting_current_loop(code: str) -> bool:
    dead_spans = proven_dead_branch_spans(code)
    nested_loop_bodies = _while_body_spans(code)
    for statement in _BREAK.finditer(code):
        offset = statement.start()
        if any(start <= offset < end for start, end in dead_spans):
            continue
        if any(start <= offset < end for start, end in nested_loop_bodies):
            continue
        return True
    return False


def _while_body_spans(code: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        (statement.body_open + 1, statement.body_close)
        for statement in control_flow_statements(code)
        if statement.kind == "while"
    )


def _next_non_space(code: str, offset: int) -> int | None:
    while offset < len(code) and code[offset].isspace():
        offset += 1
    return None if offset >= len(code) else offset


def _matching_delimiter(
    code: str, opening: int, opener: str, closer: str
) -> int | None:
    depth = 0
    for offset in range(opening, len(code)):
        character = code[offset]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return offset
            if depth < 0:
                return None
    return None


def _is_else_if(code: str, if_offset: int) -> bool:
    cursor = if_offset - 1
    while cursor >= 0 and code[cursor].isspace():
        cursor -= 1
    end = cursor + 1
    while cursor >= 0 and (code[cursor].isalnum() or code[cursor] == "_"):
        cursor -= 1
    return code[cursor + 1 : end] == "else"


def _matching_brace(code: str, opening: int) -> int | None:
    depth = 0
    for offset in range(opening, len(code)):
        character = code[offset]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return offset
            if depth < 0:
                return None
    return None
