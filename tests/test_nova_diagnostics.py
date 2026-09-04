from __future__ import annotations

from typing import Any

from mini_language_server.nova import NovaLanguageServer


def request(method: str, request_id: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaLanguageServer:
    server = NovaLanguageServer()
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None
    return server


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


def test_unresolved_function_call_publishes_versioned_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/unresolved.nova"
    open_nova(server, uri, 1, "fn main() { missing() }\n")

    notifications = server.drain_notifications()
    assert notifications == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "version": 1,
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
            },
        }
    ]

    semantic = server.semantics.get(uri)
    diagnostic = server.diagnostics.get(uri)
    assert semantic is not None
    assert diagnostic is not None
    assert diagnostic.semantic is semantic


def test_duplicate_declarations_and_ambiguous_call_are_deterministic() -> None:
    server = initialized_server()
    uri = "file:///workspace/duplicate.nova"
    open_nova(server, uri, 1, "fn foo() {}\nfn foo() {}\nfn main() { foo() }\n")

    notifications = server.drain_notifications()
    assert len(notifications) == 1
    diagnostics = notifications[0]["params"]["diagnostics"]
    assert [diagnostic["code"] for diagnostic in diagnostics] == [
        "nova.duplicate-function",
        "nova.duplicate-function",
        "nova.ambiguous-function",
    ]
    assert [diagnostic["message"] for diagnostic in diagnostics] == [
        "duplicate function declaration 'foo'",
        "duplicate function declaration 'foo'",
        "ambiguous function call 'foo'",
    ]


def test_did_change_replaces_old_diagnostics_with_current_empty_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/change.nova"
    open_nova(server, uri, 1, "fn main() { missing() }\n")
    old_diagnostic = server.diagnostics.get(uri)
    assert old_diagnostic is not None
    server.drain_notifications()

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn missing() {}\nfn main() { missing() }\n"}
                ],
            },
        )
    )

    current_semantic = server.semantics.get(uri)
    current_diagnostic = server.diagnostics.get(uri)
    assert current_semantic is not None
    assert current_diagnostic is not None
    assert current_diagnostic is not old_diagnostic
    assert current_diagnostic.semantic is current_semantic
    assert current_diagnostic.version == 2
    assert current_diagnostic.diagnostics == ()

    notifications = server.drain_notifications()
    assert notifications[-1]["params"] == {
        "uri": uri,
        "version": 2,
        "diagnostics": [],
    }


def test_same_version_reanalysis_rebinds_diagnostics_to_exact_semantic_parent() -> None:
    server = initialized_server()
    uri = "file:///workspace/reanalysis.nova"
    open_nova(server, uri, 1, "fn main() { missing() }\n")
    document = server.documents.get(uri)
    first_semantic = server.semantics.get(uri)
    first_diagnostic = server.diagnostics.get(uri)
    assert document is not None
    assert first_semantic is not None
    assert first_diagnostic is not None
    server.drain_notifications()

    second_semantic = server.nova_adapter.publish(server, document)
    second_diagnostic = server.diagnostics.get(uri)

    assert second_semantic is not first_semantic
    assert second_semantic.version == first_semantic.version == 1
    assert second_diagnostic is not None
    assert second_diagnostic is not first_diagnostic
    assert second_diagnostic.semantic is second_semantic
    assert server.semantics.get(uri) is second_semantic
    assert server.drain_notifications()[-1]["params"]["version"] == 1


def test_close_reopen_cannot_reuse_old_diagnostic_parent() -> None:
    server = initialized_server()
    uri = "file:///workspace/reopen.nova"
    open_nova(server, uri, 1, "fn main() { missing() }\n")
    old_semantic = server.semantics.get(uri)
    old_diagnostic = server.diagnostics.get(uri)
    assert old_semantic is not None
    assert old_diagnostic is not None
    server.drain_notifications()

    server.handle(
        notification("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    close_notifications = server.drain_notifications()
    assert close_notifications[-1]["params"] == {"uri": uri, "diagnostics": []}
    assert server.diagnostics.get(uri) is None

    open_nova(server, uri, 1, "fn main() { other() }\n")
    current_semantic = server.semantics.get(uri)
    current_diagnostic = server.diagnostics.get(uri)
    assert current_semantic is not None
    assert current_semantic is not old_semantic
    assert current_diagnostic is not None
    assert current_diagnostic is not old_diagnostic
    assert current_diagnostic.semantic is current_semantic
    assert server.drain_notifications()[-1]["params"]["diagnostics"][0]["code"] == (
        "nova.unresolved-function"
    )
