"""Exact-workspace Nova inlay hints for inferred function return types."""

from __future__ import annotations

from typing import Any

from .inferred_function_returns import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .source import SourceText, Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Surface conservative inferred Nova function results through inlay hints."""

    def _additional_inlay_hints(
        self,
        semantics: Any,
        source: SourceText,
        *,
        start_offset: int,
        end_offset: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Add inferred return hints inside the shared exact-workspace publication gate."""
        hints = super()._additional_inlay_hints(
            semantics,
            source,
            start_offset=start_offset,
            end_offset=end_offset,
        )
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return hints

        code = self.nova_adapter.code_view(source.text)
        for symbol in semantics.symbols.symbols:
            if symbol.kind != "function":
                continue

            opening = code.find("(", symbol.span.end)
            if opening < 0 or code[symbol.span.end:opening].strip():
                continue
            closing = self._matching_paren(code, opening)
            if closing is None:
                continue
            insertion_offset = closing + 1
            if not (start_offset <= insertion_offset < end_offset):
                continue

            declarations = tuple(
                declaration
                for declaration in self.workspace_symbols.declarations(symbol.name)
                if declaration.symbol.kind == "function"
            )
            if len(declarations) != 1:
                continue
            declaration = declarations[0]
            if declaration.snapshot is not semantics or declaration.symbol != symbol:
                continue

            signature = self._function_signature(declaration)
            if "->" in self.nova_adapter.code_view(signature):
                continue
            inferred = self._bounded_function_return_type(declaration)
            if inferred is None:
                continue

            insertion_span = Span(insertion_offset, insertion_offset)
            insertion_range = self._range(source, insertion_span)
            annotation = f" -> {inferred}"
            hints.append(
                (
                    insertion_offset,
                    {
                        "position": insertion_range["start"],
                        "label": annotation,
                        "kind": 1,
                        "textEdits": [
                            {
                                "range": insertion_range,
                                "newText": annotation,
                            }
                        ],
                        "paddingLeft": True,
                    },
                )
            )
        return hints
