from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


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


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeAction": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["codeActionProvider"] is True


def open_nova(
    server: NovaProductLanguageServer, uri: str, version: int, text: str
) -> None:
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
    server: NovaProductLanguageServer,
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
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_unresolved_name_quick_fix_declares_local_before_use() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { missing }\n")

    result = code_action(server, uri, 2, 0, 12, 19)
    assert result["result"] == [
        {
            "title": "Declare local 'missing'",
            "kind": "quickfix",
            "diagnostics": [
                {
                    "range": {
                        "start": {"line": 0, "character": 12},
                        "end": {"line": 0, "character": 19},
                    },
                    "severity": 1,
                    "message": "unresolved name 'missing'",
                    "code": "nova.unresolved-name",
                    "source": "nova",
                }
            ],
            "edit": {
                "changes": {
                    uri: [
                        {
                            "range": {
                                "start": {"line": 0, "character": 12},
                                "end": {"line": 0, "character": 12},
                            },
                            "newText": "let missing = 0 ",
                        }
                    ]
                }
            },
        }
    ]


def test_did_change_replaces_unresolved_name_quick_fix() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { old_name }\n")
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() { current_name }\n"}],
            },
        )
    )

    result = code_action(server, uri, 3, 0, 12, 24)
    assert [action["title"] for action in result["result"]] == [
        "Declare local 'current_name'"
    ]


def test_close_reopen_uses_new_unresolved_name_diagnostic_identity() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { first_name }\n")
    previous = server.diagnostics.get(uri)
    assert previous is not None

    server.handle(
        notification("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    open_nova(server, uri, 1, "fn main() { second_name }\n")
    current = server.diagnostics.get(uri)
    assert current is not None and current is not previous

    result = code_action(server, uri, 4, 0, 12, 23)
    assert [action["title"] for action in result["result"]] == [
        "Declare local 'second_name'"
    ]


class ReplacingDiagnosticsServer(NovaProductLanguageServer):
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


def test_same_version_diagnostic_replacement_suppresses_stale_quick_fix() -> None:
    server = ReplacingDiagnosticsServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { missing }\n")

    result = code_action(server, uri, 5, 0, 12, 19)
    assert result == {
        "jsonrpc": "2.0",
        "id": 5,
        "error": {"code": -32801, "message": "Content modified"},
    }


class BlockingActionsServer(NovaProductLanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def _nova_code_actions(
        self,
        uri,
        document,
        source,
        diagnostics,
        start_offset,
        end_offset,
    ):
        self.entered.set()
        assert self.release.wait(timeout=5)
        return super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )


def test_cancellation_suppresses_inflight_unresolved_name_quick_fix() -> None:
    server = BlockingActionsServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 1, "fn main() { missing }\n")
    responses: list[dict[str, Any] | None] = []

    thread = Thread(
        target=lambda: responses.append(code_action(server, uri, 41, 0, 12, 19))
    )
    thread.start()
    assert server.entered.wait(timeout=5)
    assert server.handle(notification("$/cancelRequest", {"id": 41})) is None
    server.release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 41,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
    assert len(server.requests) == 0
