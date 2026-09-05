from __future__ import annotations

from typing import Any

from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(
    method: str,
    request_id: int,
    params: dict[str, Any] | None = None,
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


def initialized_server() -> WorkspaceNovaLanguageServer:
    server = WorkspaceNovaLanguageServer()
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None
    return server


def open_nova(server: WorkspaceNovaLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notification(
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


def latest_codes(server: WorkspaceNovaLanguageServer, uri: str) -> list[str]:
    notifications = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "textDocument/publishDiagnostics"
        and item.get("params", {}).get("uri") == uri
    ]
    assert notifications
    return [
        diagnostic["code"] for diagnostic in notifications[-1]["params"]["diagnostics"]
    ]


def test_cross_file_definition_clears_unresolved_call_diagnostic() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    open_nova(server, caller, "fn caller() { target() }\n")
    assert latest_codes(server, caller) == ["nova.unresolved-function"]

    open_nova(server, "file:///workspace/library.nova", "fn target() {}\n")
    assert latest_codes(server, caller) == []


def test_duplicate_workspace_definitions_make_call_ambiguous_deterministically() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target() {}\n")
    server.drain_notifications()
    open_nova(server, caller, "fn caller() { target() }\n")
    assert latest_codes(server, caller) == []

    open_nova(server, "file:///workspace/b.nova", "fn target() {}\n")
    assert latest_codes(server, caller) == ["nova.ambiguous-function"]


def test_did_change_reconciles_other_open_documents() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    library = "file:///workspace/library.nova"
    open_nova(server, caller, "fn caller() { target() }\n")
    server.drain_notifications()
    open_nova(server, library, "fn target() {}\n")
    assert latest_codes(server, caller) == []

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn other() {}\n"}],
            },
        )
    )
    assert latest_codes(server, caller) == ["nova.unresolved-function"]


def test_close_and_reopen_rebind_workspace_diagnostics_to_current_snapshots() -> None:
    server = initialized_server()
    caller = "file:///workspace/caller.nova"
    library = "file:///workspace/library.nova"
    open_nova(server, caller, "fn caller() { target() }\n")
    server.drain_notifications()
    open_nova(server, library, "fn target() {}\n")
    assert latest_codes(server, caller) == []

    server.handle(
        notification("textDocument/didClose", {"textDocument": {"uri": library}})
    )
    assert latest_codes(server, caller) == ["nova.unresolved-function"]

    open_nova(server, library, "fn target() {}\n")
    assert latest_codes(server, caller) == []
    caller_semantic = server.semantics.get(caller)
    caller_diagnostics = server.diagnostics.get(caller)
    assert caller_semantic is not None
    assert caller_diagnostics is not None
    assert caller_diagnostics.semantic is caller_semantic
