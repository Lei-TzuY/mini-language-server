from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_params(*, properties: list[str] | None = None) -> dict[str, Any]:
    code_action: dict[str, Any] = {}
    if properties is not None:
        code_action["resolveSupport"] = {"properties": properties}
    return {"capabilities": {"textDocument": {"codeAction": code_action}}}


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
) -> None:
    server.handle(
        notify(
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


def code_action_params(uri: str) -> dict[str, Any]:
    return {
        "textDocument": {"uri": uri},
        "range": {
            "start": {"line": 0, "character": 12},
            "end": {"line": 0, "character": 19},
        },
        "context": {"diagnostics": []},
    }


def unresolved_action(server: NovaProductLanguageServer, uri: str) -> dict[str, Any]:
    response = server.handle(request("textDocument/codeAction", 2, code_action_params(uri)))
    assert response is not None
    assert len(response["result"]) == 1
    return response["result"][0]


def test_code_action_resolve_is_negotiated_and_defers_edit() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(request("initialize", 1, initialize_params(properties=["edit"])))
    assert initialized is not None
    assert initialized["result"]["capabilities"]["codeActionProvider"] == {
        "resolveProvider": True
    }

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { missing() }\n")
    action = unresolved_action(server, uri)
    assert action["title"] == "Create function 'missing'"
    assert "edit" not in action
    assert action["data"]["novaCodeActionResolve"] >= 1

    resolved = server.handle(request("codeAction/resolve", 3, action))
    assert resolved is not None
    assert resolved["result"]["title"] == "Create function 'missing'"
    assert resolved["result"]["edit"] == {
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
    }


def test_unsupported_resolve_property_keeps_eager_code_actions() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(
        request("initialize", 1, initialize_params(properties=["command"]))
    )
    assert initialized is not None
    assert initialized["result"]["capabilities"]["codeActionProvider"] is True

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { missing() }\n")
    action = unresolved_action(server, uri)
    assert "edit" in action
    assert "data" not in action


def test_same_version_diagnostic_replacement_rejects_stale_code_action_resolve() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params(properties=["edit"])))
    uri = "file:///workspace/main.nova"
    text = "fn main() { missing() }\n"
    open_nova(server, uri, text)
    action = unresolved_action(server, uri)

    document = server.documents.get(uri)
    assert document is not None
    server.nova_adapter.publish(server, document)

    assert server.handle(request("codeAction/resolve", 3, action)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_close_reopen_rejects_stale_code_action_resolve() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params(properties=["edit"])))
    uri = "file:///workspace/main.nova"
    text = "fn main() { missing() }\n"
    open_nova(server, uri, text)
    action = unresolved_action(server, uri)

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, text, version=1)

    assert server.handle(request("codeAction/resolve", 3, action)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_code_action_resolve_honors_cancellation_before_publication(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params(properties=["edit"])))
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { missing() }\n")
    action = unresolved_action(server, uri)

    entered = Event()
    release = Event()
    original = server.workspace_symbols.commit_snapshots_if_current

    def blocked_commit(snapshots, commit):
        entered.set()
        assert release.wait(timeout=5)
        return original(snapshots, commit)

    monkeypatch.setattr(server.workspace_symbols, "commit_snapshots_if_current", blocked_commit)
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            server.handle(request("codeAction/resolve", 41, action))
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(notify("$/cancelRequest", {"id": 41}))
    release.set()
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
