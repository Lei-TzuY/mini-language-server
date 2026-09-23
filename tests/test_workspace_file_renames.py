from __future__ import annotations

from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic import SemanticError


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server(
    *,
    workspace_folders: list[dict[str, str]] | None = None,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    params: dict[str, Any] = {
        "capabilities": {
            "workspace": {
                "fileOperations": {"didRename": True},
                "workspaceFolders": workspace_folders is not None,
            }
        }
    }
    if workspace_folders is not None:
        params["workspaceFolders"] = workspace_folders
    response = server.handle(request("initialize", 1, params))
    assert response is not None
    workspace = response["result"]["capabilities"]["workspace"]
    assert workspace["fileOperations"]["didRename"] == {
        "filters": [
            {
                "scheme": "file",
                "pattern": {"glob": "**/*.nova"},
            }
        ]
    }
    return server


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


def definition(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    name: str,
    request_id: int,
):
    offset = text.index(name) + 1
    return server.handle(
        request(
            "textDocument/definition",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )


def rename_files(
    server: NovaProductLanguageServer,
    *renames: tuple[str, str],
) -> None:
    server.handle(
        notify(
            "workspace/didRenameFiles",
            {
                "files": [
                    {"oldUri": old_uri, "newUri": new_uri}
                    for old_uri, new_uri in renames
                ]
            },
        )
    )


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    }


def test_open_nova_rename_rekeys_exact_workspace_identity() -> None:
    server = initialized_server()
    old_uri = "file:///workspace/helper.nova"
    new_uri = "file:///workspace/renamed.nova"
    caller_uri = "file:///workspace/main.nova"
    helper_text = "fn helper() -> Int { return 1; }\n"
    caller_text = "fn main() -> Int { return helper(); }\n"
    open_nova(server, old_uri, helper_text, version=7)
    open_nova(server, caller_uri, caller_text)
    server.drain_notifications()

    before = definition(server, caller_uri, caller_text, "helper", 2)
    assert before is not None
    assert before["result"]["uri"] == old_uri
    previous_document = server.documents.get(old_uri)
    previous_semantic = server.semantics.get(old_uri)
    assert previous_document is not None
    assert previous_semantic is not None

    rename_files(server, (old_uri, new_uri))

    assert server.documents.get(old_uri) is None
    current_document = server.documents.get(new_uri)
    assert current_document is not None
    assert current_document.version == previous_document.version
    assert current_document.text == previous_document.text
    assert current_document is not previous_document

    assert server.syntax.get(old_uri) is None
    assert server.symbols.get(old_uri) is None
    assert server.semantics.get(old_uri) is None
    assert server.diagnostics.get(old_uri) is None
    current_semantic = server.semantics.get(new_uri)
    assert current_semantic is not None
    assert current_semantic is not previous_semantic
    assert current_semantic.symbols.syntax.document is current_document
    assert server.workspace_symbols.get(old_uri) is None
    assert server.workspace_symbols.get(new_uri) is current_semantic

    after = definition(server, caller_uri, caller_text, "helper", 3)
    assert after is not None
    assert after["result"]["uri"] == new_uri
    assert "nova.unresolved-function" not in diagnostic_codes(server, caller_uri)

    notifications = server.drain_notifications()
    clears = [
        item
        for item in notifications
        if item.get("method") == "textDocument/publishDiagnostics"
        and item.get("params", {}).get("uri") == old_uri
    ]
    assert clears
    assert clears[-1]["params"]["diagnostics"] == []


def test_file_rename_destination_collision_keeps_workspace_unchanged() -> None:
    server = initialized_server()
    old_uri = "file:///workspace/helper.nova"
    occupied_uri = "file:///workspace/occupied.nova"
    caller_uri = "file:///workspace/main.nova"
    helper_text = "fn helper() -> Int { return 1; }\n"
    caller_text = "fn main() -> Int { return helper(); }\n"
    open_nova(server, old_uri, helper_text)
    open_nova(server, occupied_uri, "fn occupied() {}\n")
    open_nova(server, caller_uri, caller_text)
    before_document = server.documents.get(old_uri)
    before_workspace = server.workspace_symbols.snapshots()
    server.drain_notifications()

    rename_files(server, (old_uri, occupied_uri))

    assert server.documents.get(old_uri) is before_document
    assert server.documents.get(occupied_uri) is not None
    after_workspace = server.workspace_symbols.snapshots()
    assert after_workspace.generation == before_workspace.generation
    assert tuple(after_workspace) == tuple(before_workspace)
    response = definition(server, caller_uri, caller_text, "helper", 2)
    assert response is not None
    assert response["result"]["uri"] == old_uri
    assert server.drain_notifications() == []


def test_rename_out_of_workspace_rebuilds_local_semantics_but_drops_workspace_symbol() -> None:
    server = initialized_server(
        workspace_folders=[
            {"uri": "file:///workspace/project", "name": "project"},
        ]
    )
    old_uri = "file:///workspace/project/helper.nova"
    new_uri = "file:///workspace/outside/helper.nova"
    caller_uri = "file:///workspace/project/main.nova"
    caller_text = "fn main() -> Int { return helper(); }\n"
    open_nova(server, old_uri, "fn helper() -> Int { return 1; }\n")
    open_nova(server, caller_uri, caller_text)
    server.drain_notifications()

    rename_files(server, (old_uri, new_uri))

    assert server.documents.get(new_uri) is not None
    assert server.semantics.get(new_uri) is not None
    assert server.workspace_symbols.get(new_uri) is None
    assert server.workspace_symbols.get(old_uri) is None
    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)
    response = definition(server, caller_uri, caller_text, "helper", 2)
    assert response is not None
    assert response["result"] is None


def test_unnegotiated_file_rename_notification_does_not_mutate_documents() -> None:
    server = NovaProductLanguageServer()
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None
    assert "fileOperations" not in response["result"]["capabilities"].get(
        "workspace", {}
    )

    old_uri = "file:///workspace/helper.nova"
    new_uri = "file:///workspace/renamed.nova"
    open_nova(server, old_uri, "fn helper() {}\n")
    previous = server.documents.get(old_uri)

    rename_files(server, (old_uri, new_uri))

    assert server.documents.get(old_uri) is previous
    assert server.documents.get(new_uri) is None

def test_malformed_file_rename_batch_is_rejected_before_any_rekey() -> None:
    server = initialized_server()
    old_uri = "file:///workspace/helper.nova"
    new_uri = "file:///workspace/renamed.nova"
    open_nova(server, old_uri, "fn helper() {}\n")
    previous_document = server.documents.get(old_uri)
    previous_workspace = server.workspace_symbols.snapshots()
    server.drain_notifications()

    server.handle(
        notify(
            "workspace/didRenameFiles",
            {
                "files": [
                    {"oldUri": old_uri, "newUri": new_uri},
                    {"oldUri": "file:///workspace/other.nova"},
                ]
            },
        )
    )

    assert server.documents.get(old_uri) is previous_document
    assert server.documents.get(new_uri) is None
    current_workspace = server.workspace_symbols.snapshots()
    assert current_workspace.generation == previous_workspace.generation
    assert tuple(current_workspace) == tuple(previous_workspace)
    assert server.drain_notifications() == []


def test_file_rename_does_not_swallow_semantic_publication_invariant_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = initialized_server()
    old_uri = "file:///workspace/helper.nova"
    new_uri = "file:///workspace/renamed.nova"
    open_nova(server, old_uri, "fn helper() {}\n")

    def fail_publish(*args: Any, **kwargs: Any):
        raise SemanticError("injected publication invariant failure")

    monkeypatch.setattr(server.nova_adapter, "publish", fail_publish)

    with pytest.raises(SemanticError, match="publication invariant failure"):
        rename_files(server, (old_uri, new_uri))
