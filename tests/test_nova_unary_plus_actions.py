from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": {"codeAction": {}}}})
    )
    assert result is not None
    assert result["result"]["capabilities"]["codeActionProvider"] is True
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": text,
                }
            },
        )
    )


def code_action(
    server: NovaProductLanguageServer, uri: str, request_id: int, character: int
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": character},
                    "end": {"line": 0, "character": character + 1},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_unary_plus_quick_fix_removes_only_exact_operator() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = +input; }\n"
    open_nova(server, uri, text)
    plus = text.index("+")

    result = code_action(server, uri, 2, plus)
    actions = [item for item in result["result"] if item["title"] == "Remove unsupported unary '+'"]
    assert len(actions) == 1
    edit = actions[0]["edit"]["changes"][uri]
    assert edit == [
        {
            "range": {
                "start": {"line": 0, "character": plus},
                "end": {"line": 0, "character": plus + 1},
            },
            "newText": "",
        }
    ]
    assert actions[0]["diagnostics"][0]["code"] == "nova.unary-plus"


def test_binary_plus_has_no_unary_plus_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(a: Int, b: Int) { let value = a + b; }\n"
    open_nova(server, uri, text)
    plus = text.index("+")

    result = code_action(server, uri, 3, plus)
    assert all(item["title"] != "Remove unsupported unary '+'" for item in result["result"])


class ReplacingServer(NovaProductLanguageServer):
    def _nova_code_actions(self, uri, document, source, diagnostics, start_offset, end_offset):
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        self.nova_adapter.publish(self, document)
        return actions


def test_same_version_replacement_rejects_stale_unary_plus_action() -> None:
    server = ReplacingServer()
    result = server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": {"codeAction": {}}}})
    )
    assert result is not None
    uri = "file:///workspace/main.nova"
    text = "fn main(input: Int) { let value = +input; }\n"
    open_nova(server, uri, text)
    plus = text.index("+")

    result = code_action(server, uri, 4, plus)
    assert result == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_close_reopen_does_not_reuse_old_unary_plus_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    old_text = "fn main(input: Int) { let value = +input; }\n"
    open_nova(server, uri, old_text)
    old = server.diagnostics.get(uri)
    assert old is not None

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    new_text = "fn main(input: Int) { let value = input + 1; }\n"
    open_nova(server, uri, new_text)
    current = server.diagnostics.get(uri)
    assert current is not None and current is not old

    plus = new_text.index("+")
    result = code_action(server, uri, 5, plus)
    assert all(item["title"] != "Remove unsupported unary '+'" for item in result["result"])
