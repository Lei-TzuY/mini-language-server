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
    will_rename: bool = False,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    params: dict[str, Any] = {
        "capabilities": {
            "workspace": {
                "fileOperations": {
                    "didRename": True,
                    "willRename": will_rename,
                },
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
    if will_rename:
        assert workspace["fileOperations"]["willRename"] == {
            "filters": [
                {
                    "scheme": "file",
                    "pattern": {"glob": "**/*.nova"},
                }
            ]
        }
    else:
        assert "willRename" not in workspace["fileOperations"]
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


def will_rename_files(
    server: NovaProductLanguageServer,
    *renames: tuple[str, str],
    request_id: int = 20,
):
    return server.handle(
        request(
            "workspace/willRenameFiles",
            request_id,
            {
                "files": [
                    {"oldUri": old_uri, "newUri": new_uri}
                    for old_uri, new_uri in renames
                ]
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

def test_will_rename_preflights_valid_batch_without_mutating_state() -> None:
    server = initialized_server(will_rename=True)
    old_uri = "file:///workspace/helper.nova"
    new_uri = "file:///workspace/renamed.nova"
    open_nova(server, old_uri, "fn helper() {}\n", version=4)
    previous_document = server.documents.get(old_uri)
    previous_workspace = server.workspace_symbols.snapshots()
    server.drain_notifications()

    response = will_rename_files(server, (old_uri, new_uri), request_id=21)

    assert response == {"jsonrpc": "2.0", "id": 21, "result": None}
    assert server.documents.get(old_uri) is previous_document
    assert server.documents.get(new_uri) is None
    current_workspace = server.workspace_symbols.snapshots()
    assert current_workspace.generation == previous_workspace.generation
    assert tuple(current_workspace) == tuple(previous_workspace)
    assert server.drain_notifications() == []


def test_will_rename_rejects_open_destination_collision_without_mutation() -> None:
    server = initialized_server(will_rename=True)
    old_uri = "file:///workspace/helper.nova"
    occupied_uri = "file:///workspace/occupied.nova"
    open_nova(server, old_uri, "fn helper() {}\n")
    open_nova(server, occupied_uri, "fn occupied() {}\n")
    previous = server.documents.get(old_uri)
    previous_workspace = server.workspace_symbols.snapshots()
    server.drain_notifications()

    response = will_rename_files(server, (old_uri, occupied_uri), request_id=22)

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "destination already open" in response["error"]["message"]
    assert server.documents.get(old_uri) is previous
    assert server.documents.get(occupied_uri) is not None
    current_workspace = server.workspace_symbols.snapshots()
    assert current_workspace.generation == previous_workspace.generation
    assert tuple(current_workspace) == tuple(previous_workspace)
    assert server.drain_notifications() == []


def test_will_rename_rejects_duplicate_destination_batch() -> None:
    server = initialized_server(will_rename=True)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    target_uri = "file:///workspace/c.nova"
    open_nova(server, first_uri, "fn a() {}\n")
    open_nova(server, second_uri, "fn b() {}\n")

    response = will_rename_files(
        server,
        (first_uri, target_uri),
        (second_uri, target_uri),
        request_id=23,
    )

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "destinations must be unique" in response["error"]["message"]
    assert server.documents.get(first_uri) is not None
    assert server.documents.get(second_uri) is not None
    assert server.documents.get(target_uri) is None


def test_will_rename_malformed_params_are_invalid_without_mutation() -> None:
    server = initialized_server(will_rename=True)
    old_uri = "file:///workspace/a.nova"
    open_nova(server, old_uri, "fn a() {}\n")
    previous = server.documents.get(old_uri)

    response = server.handle(
        request(
            "workspace/willRenameFiles",
            24,
            {"files": [{"oldUri": old_uri}]},
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 24,
        "error": {"code": -32602, "message": "Invalid params"},
    }
    assert server.documents.get(old_uri) is previous


def test_will_rename_is_independent_from_did_rename_negotiation() -> None:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "fileOperations": {
                            "willRename": True,
                            "didRename": False,
                        }
                    }
                }
            },
        )
    )
    assert response is not None
    operations = response["result"]["capabilities"]["workspace"]["fileOperations"]
    assert "willRename" in operations
    assert "didRename" not in operations

    old_uri = "file:///workspace/a.nova"
    new_uri = "file:///workspace/b.nova"
    open_nova(server, old_uri, "fn a() {}\n")

    preflight = will_rename_files(server, (old_uri, new_uri), request_id=25)
    assert preflight == {"jsonrpc": "2.0", "id": 25, "result": None}

    rename_files(server, (old_uri, new_uri))
    assert server.documents.get(old_uri) is not None
    assert server.documents.get(new_uri) is None


def test_will_rename_rejects_document_identity_drift_at_publish_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = initialized_server(will_rename=True)
    old_uri = "file:///workspace/a.nova"
    new_uri = "file:///workspace/b.nova"
    open_nova(server, old_uri, "fn a() {}\n", version=1)
    original = server.documents.commit_matching_if_current

    def replace_then_commit(documents, include, commit):
        server.documents.replace(
            uri=old_uri,
            version=2,
            text="fn a() { let value = 1; }\n",
        )
        return original(documents, include, commit)

    monkeypatch.setattr(
        server.documents,
        "commit_matching_if_current",
        replace_then_commit,
    )

    response = will_rename_files(server, (old_uri, new_uri), request_id=26)

    assert response == {
        "jsonrpc": "2.0",
        "id": 26,
        "error": {"code": -32801, "message": "Content modified"},
    }
    current = server.documents.get(old_uri)
    assert current is not None and current.version == 2
    assert server.documents.get(new_uri) is None

def test_will_rename_honors_cancellation_before_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = initialized_server(will_rename=True)
    old_uri = "file:///workspace/a.nova"
    new_uri = "file:///workspace/b.nova"
    open_nova(server, old_uri, "fn a() {}\n")
    previous = server.documents.get(old_uri)
    original = server.documents.commit_matching_if_current

    def cancel_then_commit(documents, include, commit):
        assert server.requests.cancel(27) is True
        return original(documents, include, commit)

    monkeypatch.setattr(
        server.documents,
        "commit_matching_if_current",
        cancel_then_commit,
    )

    response = will_rename_files(server, (old_uri, new_uri), request_id=27)

    assert response == {
        "jsonrpc": "2.0",
        "id": 27,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    assert server.documents.get(old_uri) is previous
    assert server.documents.get(new_uri) is None
