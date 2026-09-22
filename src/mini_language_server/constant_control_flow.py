"""Product-independent bounded control-flow facts for Nova source."""

from __future__ import annotations

import re

from .constant_values import bounded_boolean_constant_value

_IF_BRANCH = re.compile(r"\bif\b(?P<condition>[^{};]*)\{")
_ELSE_IF_BRANCH = re.compile(r"\s*else\s+if\b(?P<condition>[^{};]*)\{")
_ELSE_BRANCH = re.compile(r"\s*else\s*\{")
_WHILE = re.compile(r"\bwhile\s*\(")
_BREAK = re.compile(r"\bbreak\b")


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
    for statement in _WHILE.finditer(code):
        condition_open = statement.end() - 1
        condition_close = _matching_delimiter(code, condition_open, "(", ")")
        if condition_close is None:
            continue
        body_open = _next_non_space(code, condition_close + 1)
        if body_open is None or code[body_open] != "{":
            continue
        body_close = _matching_brace(code, body_open)
        if body_close is None:
            continue
        if (
            bounded_boolean_constant_value(
                code[condition_open + 1 : condition_close]
            )
            is not True
        ):
            continue

        body = code[body_open + 1 : body_close]
        if _has_reachable_break_targeting_current_loop(body):
            continue
        spans.append((statement.start(), body_close + 1))

    return tuple(spans)


def proven_unreachable_suffix_spans(
    code: str,
) -> tuple[tuple[int, int], ...]:
    """Return same-scope suffixes made unreachable by proven divergent loops."""
    scopes: list[tuple[int, int]] = []
    stack: list[int] = []
    for offset, character in enumerate(code):
        if character == "{":
            stack.append(offset)
        elif character == "}" and stack:
            opening = stack.pop()
            scopes.append((opening + 1, offset))

    spans: list[tuple[int, int]] = []
    for statement_start, statement_end in proven_non_fallthrough_while_spans(code):
        scope_end = len(code)
        containing_ends = [
            end
            for start, end in scopes
            if start <= statement_start < end
        ]
        if containing_ends:
            scope_end = min(containing_ends)
        if statement_end < scope_end:
            spans.append((statement_end, scope_end))

    spans.sort()
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
    spans: list[tuple[int, int]] = []
    for statement in _WHILE.finditer(code):
        condition_open = statement.end() - 1
        condition_close = _matching_delimiter(code, condition_open, "(", ")")
        if condition_close is None:
            continue
        body_open = _next_non_space(code, condition_close + 1)
        if body_open is None or code[body_open] != "{":
            continue
        body_close = _matching_brace(code, body_open)
        if body_close is not None:
            spans.append((body_open + 1, body_close))
    return tuple(spans)


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
