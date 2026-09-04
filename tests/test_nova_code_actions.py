from __future__ import annotations

from typing import Any

from mini_language_server.nova import NovaLanguageServer


def request(
    method: str, request_id: int, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaLanguageServer, *, code_action: bool = True
) -> dict[str, Any]:
    text_document: dict[str, Any] = {}
    if code_action:
        text_document["codeAction"] = {}
    result = server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": text_document}})
    )
    assert result is not None
    return result


def open_nova(server: NovaLanguageServer, uri: str, version: int, text: str) -> None:
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


def code_action(
    server: NovaLanguageServer,
    uri: str,
    request_id: int,
    line: int,
    start: int,
    end: int,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": line, "character": start},
                    "end": {"line": line, "character": end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert result is not None
    return result


def test_code_action_capability_is_negotiated() -> None:
    enabled = initialize(NovaLanguageServer())
    assert enabled["result"]["capabilities"]["codeActionProvider"] is True

    disabled = initialize(NovaLanguageServer(), code_action=False)
    assert "codeActionProvider" not in disabled["result"]["capabilities"]


def test_unresolved_function_quick_fix_inserts_executable_stub() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { missing() }\n")

    result = code_action(server, uri, 2, 0, 12, 19)
    assert result["result"] == [
        {
            "title": "Create function 'missing'",
            "kind": "quickfix",
            "diagnostics": [
                {
                    "range": {
                        "start": {"line": 0, "character": 12},
                        "end": {"line": 0, "character": 19},
                    },
                    "severity": 1,
                    "message": "unresolved function 'missing'",
                    "code": "nova.unresolved-function",
                    "source": "nova",
                }
            ],
            "edit": {
                "changes": {
                    uri: [
                        {
                            "range": {
                                "start": {"line": 1, "character": 0},
                                "end": {"line": 1, "character": 0},
                            },
                            "newText": "fn missing() {}\n",
                        }
                    ]
                }
            },
        }
    ]


def test_did_change_replaces_quick_fix_with_current_diagnostic() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { old() }\n")
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { new_call() }\n"}],
            },
        )
    )

    result = code_action(server, uri, 3, 0, 12, 20)
    assert [action["title"] for action in result["result"]] == [
        "Create function 'new_call'"
    ]


class ReplacingNovaServer(NovaLanguageServer):
    def _nova_code_actions(
        self,
        uri,
        document,
        source,
        diagnostics,
        start_offset,
        end_offset,
    ):
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        self.nova_adapter.publish(self, document)
        return actions


def test_same_version_diagnostic_replacement_suppresses_stale_action() -> None:
    server = ReplacingNovaServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { missing() }\n")

    result = code_action(server, uri, 4, 0, 12, 19)
    assert result == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_close_reopen_uses_new_diagnostic_identity() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { first() }\n")
    old = server.diagnostics.get(uri)
    assert old is not None

    server.handle(
        notification("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    open_nova(server, uri, 1, "fn main() { second() }\n")
    current = server.diagnostics.get(uri)
    assert current is not None and current is not old

    result = code_action(server, uri, 5, 0, 12, 18)
    assert [action["title"] for action in result["result"]] == [
        "Create function 'second'"
    ]
