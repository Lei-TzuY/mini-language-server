from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_params(*, resolve: bool = True) -> dict[str, Any]:
    completion_item: dict[str, Any] = {}
    if resolve:
        completion_item["resolveSupport"] = {"properties": ["documentation"]}
    return {
        "capabilities": {
            "textDocument": {
                "completion": {
                    "completionItem": completion_item,
                }
            }
        }
    }


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


def completion_params(uri: str, line: int, character: int) -> dict[str, Any]:
    return {
        "textDocument": {"uri": uri},
        "position": {"line": line, "character": character},
    }


def resolved_item(server: NovaProductLanguageServer, uri: str) -> dict[str, Any]:
    completion = server.handle(
        request("textDocument/completion", 2, completion_params(uri, 1, 18))
    )
    assert completion is not None
    items = completion["result"]
    helper = next(item for item in items if item["label"] == "helper")
    assert helper["data"]["novaCompletionResolve"] >= 1
    return helper


def test_completion_resolve_is_negotiated_and_adds_exact_documentation() -> None:
    server = NovaProductLanguageServer()
    initialize = server.handle(request("initialize", 1, initialize_params()))
    assert initialize is not None
    assert initialize["result"]["capabilities"]["completionProvider"] == {
        "resolveProvider": True
    }

    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { helper(1); return 0; }\n",
    )
    item = resolved_item(server, uri)

    resolved = server.handle(request("completionItem/resolve", 3, item))
    assert resolved is not None
    assert resolved["result"]["label"] == "helper"
    assert resolved["result"]["detail"].startswith("fn helper(")
    assert resolved["result"]["documentation"] == {
        "kind": "markdown",
        "value": f"```nova\n{resolved['result']['detail']}\n```",
    }


def test_completion_resolve_is_not_advertised_without_client_resolve_support() -> None:
    server = NovaProductLanguageServer()
    initialize = server.handle(request("initialize", 1, initialize_params(resolve=False)))
    assert initialize is not None
    assert initialize["result"]["capabilities"]["completionProvider"] == {
        "resolveProvider": False
    }


def test_same_version_semantic_replacement_rejects_stale_completion_resolve() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params()))
    uri = "file:///workspace/main.nova"
    text = (
        "fn helper(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { helper(1); return 0; }\n"
    )
    open_nova(server, uri, text)
    item = resolved_item(server, uri)

    document = server.documents.get(uri)
    current = server.workspace_symbols.get(uri)
    assert document is not None and current is not None
    replacement = server.nova_adapter.publish(server, document)
    server.workspace_symbols.replace(replacement, expected=current)

    assert server.handle(request("completionItem/resolve", 3, item)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_close_reopen_rejects_stale_completion_resolve() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params()))
    uri = "file:///workspace/main.nova"
    text = (
        "fn helper(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { helper(1); return 0; }\n"
    )
    open_nova(server, uri, text)
    item = resolved_item(server, uri)

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, text, version=1)

    assert server.handle(request("completionItem/resolve", 3, item)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_completion_resolve_honors_cancellation_before_publication(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params()))
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper(value: Int) -> Int { return value; }\n"
        "fn main() -> Int { helper(1); return 0; }\n",
    )
    item = resolved_item(server, uri)

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
            server.handle(request("completionItem/resolve", 41, item))
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
