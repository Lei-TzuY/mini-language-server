from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer


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


def watched_server(root: Path) -> tuple[NovaProductLanguageServer, dict[str, Any]]:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "workspaceFolders": True,
                        "didChangeWatchedFiles": {
                            "dynamicRegistration": True,
                        },
                    }
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"},
                ],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
    queued = server.drain_server_requests()
    assert len(queued) == 1
    registration = queued[0]
    assert registration["method"] == "client/registerCapability"
    assert registration["params"] == {
        "registrations": [
            {
                "id": "mini-language-server.closed-workspace.didChangeWatchedFiles",
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
    return server, registration


def complete_registration(
    server: NovaProductLanguageServer,
    registration: dict[str, Any],
    *,
    error: dict[str, Any] | None = None,
) -> None:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": registration["id"],
    }
    if error is None:
        message["result"] = None
    else:
        message["error"] = error
    assert server.handle(message) is None


def watch(
    server: NovaProductLanguageServer,
    *changes: tuple[str, int],
) -> None:
    server.handle(
        notify(
            "workspace/didChangeWatchedFiles",
            {
                "changes": [
                    {"uri": uri, "type": change_type}
                    for uri, change_type in changes
                ]
            },
        )
    )


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
    *,
    request_id: int,
) -> dict[str, Any]:
    offset = text.index(name) + 1
    response = server.handle(
        request(
            "textDocument/definition",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": offset},
            },
        )
    )
    assert response is not None
    return response


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    }


def test_watcher_registration_is_required_before_notifications_apply(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library.nova"
    library.write_bytes(b"fn target() -> Int { return 1; }\n")
    server, registration = watched_server(tmp_path)
    uri = library.absolute().as_uri()
    before = server.workspace_symbols.get(uri)
    assert before is not None

    library.write_bytes(b"fn changed() -> Int { return 2; }\n")
    watch(server, (uri, 2))

    assert server.workspace_symbols.get(uri) is before

    complete_registration(server, registration)
    watch(server, (uri, 2))

    after = server.workspace_symbols.get(uri)
    assert after is not None
    assert after is not before
    assert after.symbols.syntax.document.text == "fn changed() -> Int { return 2; }\n"


def test_watched_external_edit_updates_navigation_and_diagnostics(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library.nova"
    library.write_bytes(b"fn target() -> Int { return 1; }\n")
    server, registration = watched_server(tmp_path)
    complete_registration(server, registration)

    caller = tmp_path / "main.nova"
    caller_uri = caller.absolute().as_uri()
    caller_text = "fn main() -> Int { return target(); }\n"
    open_nova(server, caller_uri, caller_text)

    before = definition(server, caller_uri, caller_text, "target", request_id=2)
    assert before["result"]["uri"] == library.absolute().as_uri()
    assert "nova.unresolved-function" not in diagnostic_codes(server, caller_uri)

    library.write_bytes(b"fn changed() -> Int { return 2; }\n")
    watch(server, (library.absolute().as_uri(), 2))

    after = definition(server, caller_uri, caller_text, "target", request_id=3)
    assert after["result"] is None
    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)


def test_watched_create_and_delete_reconcile_closed_workspace_index(
    tmp_path: Path,
) -> None:
    server, registration = watched_server(tmp_path)
    complete_registration(server, registration)
    created = tmp_path / "created.nova"
    uri = created.absolute().as_uri()
    assert server.workspace_symbols.get(uri) is None

    created.write_bytes(b"fn created() {}\n")
    watch(server, (uri, 1))

    indexed = server.workspace_symbols.get(uri)
    assert indexed is not None
    assert indexed.symbols.syntax.document.text == "fn created() {}\n"

    created.unlink()
    watch(server, (uri, 3))

    assert server.workspace_symbols.get(uri) is None


def test_watched_disk_change_never_overrides_open_buffer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.nova"
    source.write_bytes(b"fn disk() {}\n")
    server, registration = watched_server(tmp_path)
    complete_registration(server, registration)
    uri = source.absolute().as_uri()

    open_nova(server, uri, "fn editor() {}\n", version=4)
    opened = server.workspace_symbols.get(uri)
    assert opened is not None
    assert opened.symbols.syntax.document.text == "fn editor() {}\n"

    source.write_bytes(b"fn external() {}\n")
    watch(server, (uri, 2))

    current = server.workspace_symbols.get(uri)
    assert current is opened
    assert current.symbols.syntax.document.text == "fn editor() {}\n"


def test_malformed_watcher_batch_is_ignored_atomically(tmp_path: Path) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    first.write_bytes(b"fn before_first() {}\n")
    second.write_bytes(b"fn before_second() {}\n")
    server, registration = watched_server(tmp_path)
    complete_registration(server, registration)
    first_uri = first.absolute().as_uri()
    second_uri = second.absolute().as_uri()
    before_first = server.workspace_symbols.get(first_uri)
    before_second = server.workspace_symbols.get(second_uri)
    assert before_first is not None
    assert before_second is not None

    first.write_bytes(b"fn after_first() {}\n")
    second.write_bytes(b"fn after_second() {}\n")
    server.handle(
        notify(
            "workspace/didChangeWatchedFiles",
            {
                "changes": [
                    {"uri": first_uri, "type": 2},
                    {"uri": second_uri, "type": 99},
                ]
            },
        )
    )

    assert server.workspace_symbols.get(first_uri) is before_first
    assert server.workspace_symbols.get(second_uri) is before_second


def test_failed_watcher_registration_keeps_notifications_inactive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.nova"
    source.write_bytes(b"fn before() {}\n")
    server, registration = watched_server(tmp_path)
    uri = source.absolute().as_uri()
    before = server.workspace_symbols.get(uri)
    assert before is not None

    complete_registration(
        server,
        registration,
        error={"code": -32603, "message": "registration rejected"},
    )
    source.write_bytes(b"fn after() {}\n")
    watch(server, (uri, 2))

    assert server.workspace_symbols.get(uri) is before

def test_watcher_registration_can_start_after_workspace_folder_is_added(
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
                        "didChangeWatchedFiles": {
                            "dynamicRegistration": True,
                        },
                    }
                },
                "workspaceFolders": [],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
    assert server.drain_server_requests() == []

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [
                        {"uri": tmp_path.as_uri(), "name": "workspace"},
                    ],
                    "removed": [],
                }
            },
        )
    )

    queued = server.drain_server_requests()
    assert len(queued) == 1
    assert queued[0]["method"] == "client/registerCapability"
    assert queued[0]["params"]["registrations"][0]["method"] == (
        "workspace/didChangeWatchedFiles"
    )
