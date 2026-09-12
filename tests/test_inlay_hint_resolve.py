from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_params(*properties: str) -> dict[str, Any]:
    inlay_hint: dict[str, Any] = {}
    if properties:
        inlay_hint["resolveSupport"] = {"properties": list(properties)}
    return {"capabilities": {"textDocument": {"inlayHint": inlay_hint}}}


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


def hints(server: NovaProductLanguageServer, uri: str, text: str) -> list[dict[str, Any]]:
    response = server.handle(
        request(
            "textDocument/inlayHint",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": len(text.rstrip("\n"))},
                },
            },
        )
    )
    assert response is not None
    return response["result"]


def test_inlay_hint_resolve_is_negotiated_and_restores_lazy_payload() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(
        request("initialize", 1, initialize_params("tooltip", "textEdits"))
    )
    assert initialized is not None
    assert initialized["result"]["capabilities"]["inlayHintProvider"] == {
        "resolveProvider": True
    }

    uri = "file:///workspace/main.nova"
    text = "fn inferred() { return 1; }\n"
    open_nova(server, uri, text)
    item = next(hint for hint in hints(server, uri, text) if hint["label"] == " -> Int")
    assert "textEdits" not in item
    assert "tooltip" not in item
    assert item["data"]["novaInlayHintResolve"] >= 1

    resolved = server.handle(request("inlayHint/resolve", 3, item))
    assert resolved is not None
    assert resolved["result"]["tooltip"] == {
        "kind": "markdown",
        "value": "Nova inferred return type `Int`.",
    }
    assert resolved["result"]["textEdits"][0]["newText"] == " -> Int"


def test_unsupported_resolve_property_does_not_enable_resolve_provider() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(request("initialize", 1, initialize_params("location")))
    assert initialized is not None
    assert initialized["result"]["capabilities"]["inlayHintProvider"] is True


def test_same_version_workspace_replacement_rejects_stale_inlay_hint_resolve() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params("tooltip")))
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) {} fn caller() { target(1); }\n"
    open_nova(server, uri, text)
    item = next(hint for hint in hints(server, uri, text) if hint["label"] == "value:")

    document = server.documents.get(uri)
    current = server.workspace_symbols.get(uri)
    assert document is not None and current is not None
    replacement = server.nova_adapter.publish(server, document)
    server.workspace_symbols.replace(replacement, expected=current)

    assert server.handle(request("inlayHint/resolve", 3, item)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_close_reopen_rejects_stale_inlay_hint_resolve() -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params("tooltip")))
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) {} fn caller() { target(1); }\n"
    open_nova(server, uri, text)
    item = next(hint for hint in hints(server, uri, text) if hint["label"] == "value:")

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, text)
    assert server.handle(request("inlayHint/resolve", 3, item)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_inlay_hint_resolve_honors_cancellation_before_publication(monkeypatch) -> None:
    server = NovaProductLanguageServer()
    server.handle(request("initialize", 1, initialize_params("tooltip")))
    uri = "file:///workspace/main.nova"
    text = "fn target(value: Int) {} fn caller() { target(1); }\n"
    open_nova(server, uri, text)
    item = next(hint for hint in hints(server, uri, text) if hint["label"] == "value:")

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
        target=lambda: responses.append(server.handle(request("inlayHint/resolve", 41, item)))
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 41}))
    release.set()
    thread.join(timeout=5)
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 41,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
    assert len(server.requests) == 0
