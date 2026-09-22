"""Product-independent bounded control-flow facts for Nova source."""

from __future__ import annotations

import re

from .constant_values import bounded_boolean_constant_value

_IF_BRANCH = re.compile(r"\bif\b(?P<condition>[^{};]*)\{")
_ELSE_IF_BRANCH = re.compile(r"\s*else\s+if\b(?P<condition>[^{};]*)\{")
_ELSE_BRANCH = re.compile(r"\s*else\s*\{")


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
