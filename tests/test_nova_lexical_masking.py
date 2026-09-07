from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.lexical_nova import LexicalNovaFunctionAdapter


def request(method: str, request_id: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None


def open_nova(server: NovaProductLanguageServer, uri: str, version: int, text: str) -> None:
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": version,
                    "text": text,
                }
            },
        )
    )


def test_code_view_preserves_offsets_and_newlines_while_masking_trivia() -> None:
    text = 'fn main() { "ghost() }"; // hidden()\n/* fn buried() {} */ main() }\n'
    view = LexicalNovaFunctionAdapter.code_view(text)

    assert len(view) == len(text)
    assert [index for index, char in enumerate(view) if char == "\n"] == [
        index for index, char in enumerate(text) if char == "\n"
    ]
    assert "ghost" not in view
    assert "hidden" not in view
    assert "buried" not in view
    assert view.index("main") == text.index("main")
    assert view.rindex("main") == text.rindex("main")


def test_final_product_ignores_comment_and_string_pseudo_syntax() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/trivia.nova"
    text = (
        "// fn hidden() {}\n"
        "fn main() {\n"
        '  "ghost() } \\" still string";\n'
        "  /* fn buried() { missing() } */\n"
        "  // phantom()\n"
        "}\n"
    )
    open_nova(server, uri, 1, text)

    semantics = server.semantics.get(uri)
    diagnostics = server.diagnostics.get(uri)
    assert semantics is not None
    assert diagnostics is not None
    assert [(symbol.name, symbol.kind) for symbol in semantics.symbols.symbols] == [
        ("main", "function")
    ]
    assert semantics.references == ()
    assert diagnostics.diagnostics == ()


def test_trivia_change_rebuilds_exact_semantics_and_close_reopen_updates_diagnostics() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/trivia-change.nova"
    open_nova(server, uri, 1, 'fn main() { "ghost()" }\n')

    original = server.semantics.get(uri)
    assert original is not None
    first_diagnostics = server.diagnostics.get(uri)
    assert first_diagnostics is not None and first_diagnostics.diagnostics == ()

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { ghost() }\n"}],
            },
        )
    )

    changed = server.semantics.get(uri)
    changed_diagnostics = server.diagnostics.get(uri)
    assert changed is not None and changed is not original
    assert changed.version == 2
    assert changed_diagnostics is not None
    assert [diagnostic.code for diagnostic in changed_diagnostics.diagnostics] == [
        "nova.unresolved-function"
    ]

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, 1, "fn main() { /* ghost() */ }\n")

    reopened = server.semantics.get(uri)
    reopened_diagnostics = server.diagnostics.get(uri)
    assert reopened is not None and reopened is not changed
    assert reopened.symbols.syntax.document is server.documents.get(uri)
    assert reopened_diagnostics is not None and reopened_diagnostics.diagnostics == ()
