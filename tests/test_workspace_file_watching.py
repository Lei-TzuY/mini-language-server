from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def notify(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize_watching(
    server: WorkspaceNovaLanguageServer,
    root: Path,
    *,
    dynamic: bool = True,
    diagnostic_refresh: bool = False,
) -> None:
    workspace: dict[str, Any] = {
        "workspaceFolders": True,
        "didChangeWatchedFiles": {"dynamicRegistration": dynamic},
    }
    text_document: dict[str, Any] = {}
    if diagnostic_refresh:
        workspace["diagnostics"] = {"refreshSupport": True}
        text_document["diagnostic"] = {}

    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": workspace,
                    "textDocument": text_document,
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"}
                ],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))


def activate_watcher(server: WorkspaceNovaLanguageServer) -> dict[str, Any]:
    queued = server.drain_server_requests()
    assert len(queued) == 1
    registration = queued[0]
    assert registration["method"] == "client/registerCapability"
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": registration["id"],
            "result": None,
        }
    ) is None
    assert server._watched_files_registration_active is True
    return registration


def watched_change(uri: str, change_type: int) -> dict[str, Any]:
    return notify(
        "workspace/didChangeWatchedFiles",
        {"changes": [{"uri": uri, "type": change_type}]},
    )


def test_initialized_registers_bounded_nova_file_watcher(tmp_path: Path) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)

    registration = activate_watcher(server)

    assert registration["params"] == {
        "registrations": [
            {
                "id": "mini-language-server.workspace.nova-files",
                "method": "workspace/didChangeWatchedFiles",
                "registerOptions": {
                    "watchers": [
                        {
                            "globPattern": "**/*.nova",
                            "kind": 7,
                        }
                    ]
                },
            }
        ]
    }


def test_watched_change_rebuilds_closed_semantic_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "provider.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    activate_watcher(server)

    before = server.workspace_symbols.get(uri)
    assert before is not None
    source.write_text(
        "fn target(flag: Bool) { flag }\n",
        encoding="utf-8",
    )

    server.handle(watched_change(uri, 2))

    after = server.workspace_symbols.get(uri)
    assert after is not None
    assert after is not before
    assert after.symbols.syntax.document.text == (
        "fn target(flag: Bool) { flag }\n"
    )


def test_watched_create_and_delete_update_closed_index(tmp_path: Path) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    activate_watcher(server)

    source = tmp_path / "created.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server.handle(watched_change(uri, 1))
    assert server.workspace_symbols.get(uri) is not None

    source.unlink()
    server.handle(watched_change(uri, 3))
    assert server.workspace_symbols.get(uri) is None


def test_watched_notification_is_ignored_before_registration_success(
    tmp_path: Path,
) -> None:
    source = tmp_path / "provider.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)

    before = server.workspace_symbols.get(uri)
    assert before is not None
    source.write_text("fn changed() {}\n", encoding="utf-8")
    server.handle(watched_change(uri, 2))

    assert server.workspace_symbols.get(uri) is before

    activate_watcher(server)
    after_registration = server.workspace_symbols.get(uri)
    assert after_registration is not None
    assert after_registration is not before
    assert after_registration.symbols.syntax.document.text == (
        "fn changed() {}\n"
    )

    server.handle(watched_change(uri, 2))
    assert server.workspace_symbols.get(uri) is after_registration


def test_registration_error_keeps_watched_notifications_inactive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "provider.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)

    registration = server.drain_server_requests()[0]
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": registration["id"],
            "error": {
                "code": -32601,
                "message": "dynamic registration unsupported",
            },
        }
    ) is None
    assert server._watched_files_registration_active is False

    before = server.workspace_symbols.get(uri)
    source.write_text("fn changed() {}\n", encoding="utf-8")
    server.handle(watched_change(uri, 2))
    assert server.workspace_symbols.get(uri) is before


def test_client_without_dynamic_support_registers_no_watcher(
    tmp_path: Path,
) -> None:
    source = tmp_path / "provider.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path, dynamic=False)

    assert server.drain_server_requests() == []
    before = server.workspace_symbols.get(uri)
    source.write_text("fn changed() {}\n", encoding="utf-8")
    server.handle(watched_change(uri, 2))
    assert server.workspace_symbols.get(uri) is before


def test_malformed_watcher_batch_is_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "provider.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    activate_watcher(server)
    before = server.workspace_symbols.snapshots()

    source.write_text("fn changed() {}\n", encoding="utf-8")
    server.handle(
        notify(
            "workspace/didChangeWatchedFiles",
            {
                "changes": [
                    {
                        "uri": source.absolute().as_uri(),
                        "type": 4,
                    }
                ]
            },
        )
    )

    after = server.workspace_symbols.snapshots()
    assert after.generation == before.generation
    assert tuple(after) == tuple(before)


def test_non_nova_watcher_event_does_not_advance_workspace(
    tmp_path: Path,
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    activate_watcher(server)
    before = server.workspace_symbols.snapshots()

    notes = tmp_path / "notes.txt"
    notes.write_text("changed\n", encoding="utf-8")
    server.handle(watched_change(notes.absolute().as_uri(), 2))

    after = server.workspace_symbols.snapshots()
    assert after.generation == before.generation
    assert tuple(after) == tuple(before)


def test_watcher_does_not_displace_open_buffer(tmp_path: Path) -> None:
    source = tmp_path / "provider.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    registration = server.drain_server_requests()[0]

    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 7,
                    "text": "fn target(flag: Bool) { flag }\n",
                }
            },
        )
    )
    open_semantic = server.workspace_symbols.get(uri)
    assert open_semantic is not None

    server.handle(
        {
            "jsonrpc": "2.0",
            "id": registration["id"],
            "result": None,
        }
    )
    source.write_text("fn disk_only() {}\n", encoding="utf-8")
    server.handle(watched_change(uri, 2))

    assert server.workspace_symbols.get(uri) is open_semantic


def test_watched_file_transition_requests_diagnostic_refresh(
    tmp_path: Path,
) -> None:
    server = NovaProductLanguageServer()
    initialize_watching(
        server,
        tmp_path,
        diagnostic_refresh=True,
    )
    activate_watcher(server)
    assert server.drain_server_requests() == []

    source = tmp_path / "created.nova"
    source.write_text("fn main() {}\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server.handle(watched_change(uri, 1))

    first_refresh = server.drain_server_requests()
    assert len(first_refresh) == 1
    assert first_refresh[0]["method"] == "workspace/diagnostic/refresh"
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": first_refresh[0]["id"],
            "result": None,
        }
    ) is None

    first = server.handle(
        request(
            "workspace/diagnostic",
            9,
            {"previousResultIds": []},
        )
    )
    assert first is not None
    first_reports = {
        item["uri"]: item
        for item in first["result"]["items"]
    }
    assert first_reports[uri]["items"] == []

    source.write_text(
        "fn main() { missing(); }\n",
        encoding="utf-8",
    )
    server.handle(watched_change(uri, 2))

    second_refresh = server.drain_server_requests()
    assert len(second_refresh) == 1
    assert second_refresh[0]["method"] == "workspace/diagnostic/refresh"

    second = server.handle(
        request(
            "workspace/diagnostic",
            10,
            {"previousResultIds": []},
        )
    )
    assert second is not None
    reports = {item["uri"]: item for item in second["result"]["items"]}
    assert [item["code"] for item in reports[uri]["items"]] == [
        "nova.unresolved-function"
    ]

def test_duplicate_initialized_does_not_repeat_watcher_registration(
    tmp_path: Path,
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    registration = activate_watcher(server)

    server.handle(notify("initialized", {}))

    assert server.drain_server_requests() == []
    assert server._watched_files_registration_active is True
    assert registration["params"]["registrations"][0]["id"] == (
        "mini-language-server.workspace.nova-files"
    )


def test_shutdown_retracts_pending_watcher_registration(
    tmp_path: Path,
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_watching(server, tmp_path)
    registration_id = server._watched_files_registration_request
    assert registration_id is not None

    response = server.handle(request("shutdown", 90, {}))

    assert response == {
        "jsonrpc": "2.0",
        "id": 90,
        "result": None,
    }
    assert server.drain_server_requests() == []
    assert server._watched_files_registration_request is None
    assert server._watched_files_registration_active is False

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": registration_id,
            "result": None,
        }
    ) is None
    assert server._watched_files_registration_active is False

def test_watcher_and_formatting_dynamic_registrations_are_isolated(
    tmp_path: Path,
) -> None:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "workspaceFolders": True,
                        "configuration": True,
                        "didChangeConfiguration": {
                            "dynamicRegistration": True,
                        },
                        "didChangeWatchedFiles": {
                            "dynamicRegistration": True,
                        },
                    }
                },
                "workspaceFolders": [
                    {"uri": tmp_path.as_uri(), "name": "workspace"}
                ],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))

    queued = server.drain_server_requests()
    registrations = [
        item
        for item in queued
        if item["method"] == "client/registerCapability"
    ]
    configuration = next(
        item
        for item in queued
        if item["method"] == "workspace/configuration"
    )
    assert len(registrations) == 2

    watcher = next(
        item
        for item in registrations
        if item["params"]["registrations"][0]["method"]
        == "workspace/didChangeWatchedFiles"
    )
    formatting = next(
        item
        for item in registrations
        if item["params"]["registrations"][0]["method"]
        == "workspace/didChangeConfiguration"
    )

    assert server.handle(
        {"jsonrpc": "2.0", "id": watcher["id"], "result": None}
    ) is None
    assert server._watched_files_registration_active is True
    assert server._formatting_configuration_registration_active is False

    assert server.handle(
        {"jsonrpc": "2.0", "id": formatting["id"], "result": None}
    ) is None
    assert server._formatting_configuration_registration_active is True
    assert server._watched_files_registration_active is True

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": configuration["id"],
            "result": [{"tabSize": 4, "insertSpaces": True}],
        }
    ) is None
