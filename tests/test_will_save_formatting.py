from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    supported: bool = True,
    configuration: bool = False,
    workspace_folders: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    synchronization = {"willSaveWaitUntil": True} if supported else {}
    workspace: dict[str, Any] = {"configuration": configuration}
    if workspace_folders is not None:
        workspace["workspaceFolders"] = True
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": {"synchronization": synchronization},
            "workspace": workspace,
        }
    }
    if workspace_folders is not None:
        params["workspaceFolders"] = workspace_folders
    result = server.handle(request("initialize", 1, params))
    assert result is not None
    return result


def open_nova(server: NovaProductLanguageServer, uri: str, text: str, version: int = 1) -> None:
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


def will_save(server: NovaProductLanguageServer, uri: str, request_id: int = 2) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/willSaveWaitUntil",
            request_id,
            {"textDocument": {"uri": uri}, "reason": 1},
        )
    )
    assert result is not None
    return result


def test_initialize_negotiates_will_save_wait_until() -> None:
    capabilities = initialize(NovaProductLanguageServer())["result"]["capabilities"]
    assert capabilities["textDocumentSync"] == {
        "openClose": True,
        "change": 2,
        "willSaveWaitUntil": True,
    }


def test_initialize_preserves_incremental_sync_when_unsupported() -> None:
    capabilities = initialize(NovaProductLanguageServer(), supported=False)["result"][
        "capabilities"
    ]
    assert capabilities["textDocumentSync"] == 2


def test_will_save_returns_deterministic_trivia_aware_formatting_edit() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    source = "fn main() {\nlet x = \"}\"\nif true {\nreturn x\n}\n}\n"
    open_nova(server, uri, source)

    result = will_save(server, uri)["result"]

    assert result == [
        {
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 6, "character": 0},
            },
            "newText": (
                "fn main() {\n    let x = \"}\"\n    if true {\n"
                "        return x\n    }\n}\n"
            ),
        }
    ]


def test_will_save_tracks_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, uri)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert will_save(server, uri, 3) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32602, "message": "Invalid params"},
    }

    open_nova(server, uri, "fn main() {\nreturn 1\n}\n", version=1)
    assert will_save(server, uri, 4)["result"]


def test_will_save_rejects_same_version_semantic_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    original = server.semantics.get(uri)
    assert original is not None
    real_commit = server.semantics.commit_if_current

    def replace_then_commit(snapshot, callback):
        server.semantics.publish(original.symbols, original.references)
        return real_commit(snapshot, callback)

    server.semantics.commit_if_current = replace_then_commit  # type: ignore[method-assign]

    assert will_save(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_will_save_honors_cancellation_before_publication() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    real_checkpoint = server.requests.checkpoint
    checkpoints = 0

    def cancel_before_second_checkpoint(context) -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            server.handle(notify("$/cancelRequest", {"id": 9}))
        real_checkpoint(context)

    server.requests.checkpoint = cancel_before_second_checkpoint  # type: ignore[method-assign]

    assert will_save(server, uri, 9) == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32800, "message": "Request cancelled"},
    }

def test_initialized_requests_formatting_configuration_when_supported() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    assert server.drain_server_requests() == []

    assert server.handle(notify("initialized", {})) is None
    requests = server.drain_server_requests()

    assert requests == [
        {
            "jsonrpc": "2.0",
            "id": "server:1",
            "method": "workspace/configuration",
            "params": {
                "items": [
                    {"section": "mini-language-server.formatting"},
                ]
            },
        }
    ]


def test_workspace_configuration_controls_will_save_indentation() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    config_request = server.drain_server_requests()[0]

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": config_request["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    ) is None

    uri = "file:///workspace/main.nova"
    source = "fn main() {\nreturn 1\n}\n"
    open_nova(server, uri, source)

    result = will_save(server, uri)["result"]
    assert result[0]["newText"] == "fn main() {\n\treturn 1\n}\n"


def test_configuration_change_invalidates_pending_response_and_refetches() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()[0]

    server.handle(
        notify(
            "workspace/didChangeConfiguration",
            {"settings": {"ignored": "notification payload"}},
        )
    )
    assert server.drain_server_requests() == []

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    ) is None
    second = server.drain_server_requests()
    assert len(second) == 1
    assert second[0]["method"] == "workspace/configuration"
    assert second[0]["id"] != first["id"]

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": second[0]["id"],
            "result": [{"tabSize": 3, "insertSpaces": True}],
        }
    ) is None

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    result = will_save(server, uri)["result"]
    assert result[0]["newText"] == "fn main() {\n   return 1\n}\n"


def test_configuration_error_preserves_last_valid_settings_and_rearms_on_change() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    )

    server.handle(notify("workspace/didChangeConfiguration", {"settings": {}}))
    second = server.drain_server_requests()[0]
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": second["id"],
            "error": {"code": -32603, "message": "configuration unavailable"},
        }
    ) is None

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, uri)["result"][0]["newText"] == (
        "fn main() {\n\treturn 1\n}\n"
    )

    server.handle(notify("workspace/didChangeConfiguration", {"settings": {}}))
    third = server.drain_server_requests()
    assert len(third) == 1
    assert third[0]["method"] == "workspace/configuration"


def test_null_configuration_restores_default_save_formatting() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    )

    server.handle(notify("workspace/didChangeConfiguration", {"settings": {}}))
    second = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": second["id"],
            "result": [None],
        }
    )

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, uri)["result"][0]["newText"] == (
        "fn main() {\n    return 1\n}\n"
    )


def test_invalid_configuration_does_not_replace_last_valid_settings() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    )

    server.handle(notify("workspace/didChangeConfiguration", {"settings": {}}))
    second = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": second["id"],
            "result": [{"tabSize": 0, "insertSpaces": "yes"}],
        }
    )

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, uri)["result"][0]["newText"] == (
        "fn main() {\n\treturn 1\n}\n"
    )


def test_no_workspace_configuration_capability_sends_no_configuration_request() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=False)
    server.handle(notify("initialized", {}))
    server.handle(notify("workspace/didChangeConfiguration", {"settings": {}}))

    assert server.drain_server_requests() == []

def test_workspace_configuration_requests_global_and_folder_scopes() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        workspace_folders=[
            {"uri": "file:///workspace/b", "name": "b"},
            {"uri": "file:///workspace/a", "name": "a"},
        ],
    )
    server.handle(notify("initialized", {}))

    assert server.drain_server_requests() == [
        {
            "jsonrpc": "2.0",
            "id": "server:1",
            "method": "workspace/configuration",
            "params": {
                "items": [
                    {"section": "mini-language-server.formatting"},
                    {
                        "section": "mini-language-server.formatting",
                        "scopeUri": "file:///workspace/a",
                    },
                    {
                        "section": "mini-language-server.formatting",
                        "scopeUri": "file:///workspace/b",
                    },
                ]
            },
        }
    ]


def test_workspace_folder_specific_formatting_settings_drive_save_edits() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        workspace_folders=[
            {"uri": "file:///workspace/a", "name": "a"},
            {"uri": "file:///workspace/b", "name": "b"},
        ],
    )
    server.handle(notify("initialized", {}))
    config_request = server.drain_server_requests()[0]
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": config_request["id"],
            "result": [
                {"tabSize": 3, "insertSpaces": True},
                {"tabSize": 2, "insertSpaces": True},
                {"tabSize": 8, "insertSpaces": False},
            ],
        }
    ) is None

    source = "fn main() {\nreturn 1\n}\n"
    a_uri = "file:///workspace/a/main.nova"
    b_uri = "file:///workspace/b/main.nova"
    outside_uri = "file:///outside/main.nova"
    open_nova(server, a_uri, source)
    open_nova(server, b_uri, source)
    open_nova(server, outside_uri, source)

    assert will_save(server, a_uri, 20)["result"][0]["newText"] == (
        "fn main() {\n  return 1\n}\n"
    )
    assert will_save(server, b_uri, 21)["result"][0]["newText"] == (
        "fn main() {\n\treturn 1\n}\n"
    )
    assert will_save(server, outside_uri, 22)["result"][0]["newText"] == (
        "fn main() {\n   return 1\n}\n"
    )


def test_nested_workspace_folder_uses_most_specific_formatting_scope() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        workspace_folders=[
            {"uri": "file:///workspace/app", "name": "app"},
            {"uri": "file:///workspace/app/core", "name": "core"},
        ],
    )
    server.handle(notify("initialized", {}))
    config_request = server.drain_server_requests()[0]
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": config_request["id"],
            "result": [
                {"tabSize": 4, "insertSpaces": True},
                {"tabSize": 2, "insertSpaces": True},
                {"tabSize": 8, "insertSpaces": False},
            ],
        }
    ) is None

    uri = "file:///workspace/app/core/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")

    assert will_save(server, uri, 23)["result"][0]["newText"] == (
        "fn main() {\n\treturn 1\n}\n"
    )


def test_workspace_folder_change_invalidates_pending_configuration_scope_set() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        workspace_folders=[{"uri": "file:///workspace/a", "name": "a"}],
    )
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()[0]

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": "file:///workspace/b", "name": "b"}],
                    "removed": [],
                }
            },
        )
    )
    assert server.drain_server_requests() == []

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [
                {"tabSize": 3, "insertSpaces": True},
                {"tabSize": 2, "insertSpaces": True},
            ],
        }
    ) is None

    second = server.drain_server_requests()
    assert len(second) == 1
    assert second[0]["method"] == "workspace/configuration"
    assert second[0]["params"]["items"] == [
        {"section": "mini-language-server.formatting"},
        {
            "section": "mini-language-server.formatting",
            "scopeUri": "file:///workspace/a",
        },
        {
            "section": "mini-language-server.formatting",
            "scopeUri": "file:///workspace/b",
        },
    ]

    # The stale first response was retired, not applied.
    a_uri = "file:///workspace/a/main.nova"
    open_nova(server, a_uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, a_uri, 24)["result"][0]["newText"] == (
        "fn main() {\n    return 1\n}\n"
    )

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": second[0]["id"],
            "result": [
                {"tabSize": 3, "insertSpaces": True},
                {"tabSize": 2, "insertSpaces": True},
                {"tabSize": 8, "insertSpaces": False},
            ],
        }
    ) is None
    assert will_save(server, a_uri, 25)["result"][0]["newText"] == (
        "fn main() {\n  return 1\n}\n"
    )


def test_removed_workspace_folder_falls_back_to_global_configuration() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        workspace_folders=[
            {"uri": "file:///workspace/a", "name": "a"},
            {"uri": "file:///workspace/b", "name": "b"},
        ],
    )
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [
                {"tabSize": 3, "insertSpaces": True},
                {"tabSize": 2, "insertSpaces": True},
                {"tabSize": 8, "insertSpaces": False},
            ],
        }
    )

    b_uri = "file:///workspace/b/main.nova"
    source = "fn main() {\nreturn 1\n}\n"
    open_nova(server, b_uri, source)
    assert will_save(server, b_uri, 26)["result"][0]["newText"] == (
        "fn main() {\n\treturn 1\n}\n"
    )

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [],
                    "removed": [{"uri": "file:///workspace/b", "name": "b"}],
                }
            },
        )
    )

    # Scope changes immediately; until the refreshed configuration arrives,
    # the open document falls back to the last valid global setting.
    assert will_save(server, b_uri, 27)["result"][0]["newText"] == (
        "fn main() {\n   return 1\n}\n"
    )
