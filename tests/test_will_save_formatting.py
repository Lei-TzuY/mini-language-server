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
    dynamic_configuration_registration: bool = False,
    workspace_folders: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    synchronization = {"willSaveWaitUntil": True} if supported else {}
    workspace: dict[str, Any] = {"configuration": configuration}
    if dynamic_configuration_registration:
        workspace["didChangeConfiguration"] = {"dynamicRegistration": True}
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


def test_configuration_change_supersedes_pending_request_immediately() -> None:
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

    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {"id": first["id"]},
        }
    ]
    second = server.drain_server_requests()
    assert len(second) == 1
    assert second[0]["method"] == "workspace/configuration"
    assert second[0]["id"] != first["id"]

    # The retired response is ignored and cannot queue another request.
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    ) is None
    assert server.drain_server_requests() == []

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


def test_configuration_change_retracts_unsent_stale_request_locally() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))

    # The first configuration request is still in the local outbox.
    server.handle(
        notify(
            "workspace/didChangeConfiguration",
            {"settings": {}},
        )
    )

    assert server.drain_notifications() == []
    requests = server.drain_server_requests()
    assert len(requests) == 1
    assert requests[0]["method"] == "workspace/configuration"
    assert requests[0]["id"] == "server:2"


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


def test_workspace_folder_change_supersedes_pending_configuration_scope_set() -> None:
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

    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {"id": first["id"]},
        }
    ]
    second = server.drain_server_requests()
    assert len(second) == 1
    assert second[0]["method"] == "workspace/configuration"
    assert second[0]["id"] != first["id"]
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

    # The retired first response is ignored and never applies stale settings.
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
    assert server.drain_server_requests() == []

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


def test_initialized_dynamically_registers_configuration_changes() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        dynamic_configuration_registration=True,
    )

    assert server.handle(notify("initialized", {})) is None
    requests = server.drain_server_requests()

    assert requests == [
        {
            "jsonrpc": "2.0",
            "id": "server:1",
            "method": "client/registerCapability",
            "params": {
                "registrations": [
                    {
                        "id": (
                            "mini-language-server.formatting."
                            "didChangeConfiguration"
                        ),
                        "method": "workspace/didChangeConfiguration",
                        "registerOptions": {
                            "section": "mini-language-server.formatting",
                        },
                    }
                ]
            },
        },
        {
            "jsonrpc": "2.0",
            "id": "server:2",
            "method": "workspace/configuration",
            "params": {
                "items": [
                    {"section": "mini-language-server.formatting"},
                ]
            },
        },
    ]

    assert server.handle(
        {"jsonrpc": "2.0", "id": "server:1", "result": None}
    ) is None
    assert server._formatting_configuration_registration_active is True


def test_dynamic_registration_error_does_not_pollute_formatting_cache() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        dynamic_configuration_registration=True,
    )
    server.handle(notify("initialized", {}))
    registration, configuration = server.drain_server_requests()

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": configuration["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    ) is None
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": registration["id"],
            "error": {"code": -32601, "message": "registration unsupported"},
        }
    ) is None
    assert server._formatting_configuration_registration_active is False

    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    assert will_save(server, uri)["result"][0]["newText"] == (
        "fn main() {\n\treturn 1\n}\n"
    )


def test_duplicate_initialized_does_not_repeat_dynamic_registration() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        dynamic_configuration_registration=True,
    )
    server.handle(notify("initialized", {}))
    first = server.drain_server_requests()
    registration = next(
        item for item in first if item["method"] == "client/registerCapability"
    )
    configuration = next(
        item for item in first if item["method"] == "workspace/configuration"
    )
    server.handle(
        {"jsonrpc": "2.0", "id": registration["id"], "result": None}
    )
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": configuration["id"],
            "result": [{"tabSize": 4, "insertSpaces": True}],
        }
    )

    server.handle(notify("initialized", {}))
    second = server.drain_server_requests()

    assert [item["method"] for item in second] == ["workspace/configuration"]


def test_dynamic_registration_requires_workspace_configuration_support() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=False,
        dynamic_configuration_registration=True,
    )
    server.handle(notify("initialized", {}))

    assert server.drain_server_requests() == []


def test_legacy_configuration_client_keeps_configuration_only_request() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        dynamic_configuration_registration=False,
    )
    server.handle(notify("initialized", {}))

    requests = server.drain_server_requests()
    assert len(requests) == 1
    assert requests[0]["method"] == "workspace/configuration"


def test_shutdown_retires_configuration_request_and_ignores_late_response() -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    configuration = server.drain_server_requests()[0]

    assert server._formatting_configurations[None] == (4, True)
    assert configuration["id"] in server._formatting_configuration_requests

    assert server.handle(request("shutdown", 90, {})) == {
        "jsonrpc": "2.0",
        "id": 90,
        "result": None,
    }
    assert server._formatting_configuration_requests == {}
    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {"id": configuration["id"]},
        }
    ]

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": configuration["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    ) is None
    assert server._formatting_configurations[None] == (4, True)


def test_shutdown_retracts_unsent_dynamic_registration_and_configuration() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        dynamic_configuration_registration=True,
    )
    server.handle(notify("initialized", {}))

    pending = list(server._server_requests)
    registration = next(
        item for item in pending if item["method"] == "client/registerCapability"
    )
    configuration = next(
        item for item in pending if item["method"] == "workspace/configuration"
    )
    assert server._formatting_configuration_registration_request == registration["id"]
    assert configuration["id"] in server._formatting_configuration_requests

    assert server.handle(request("shutdown", 91, {})) == {
        "jsonrpc": "2.0",
        "id": 91,
        "result": None,
    }

    assert server.drain_server_requests() == []
    assert server.drain_notifications() == []
    assert server._formatting_configuration_registration_request is None
    assert server._formatting_configuration_requests == {}
    assert server._formatting_configuration_registration_active is False

    assert server.handle(
        {"jsonrpc": "2.0", "id": registration["id"], "result": None}
    ) is None
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": configuration["id"],
            "result": [{"tabSize": 2, "insertSpaces": False}],
        }
    ) is None
    assert server._formatting_configuration_registration_active is False
    assert server._formatting_configurations[None] == (4, True)


def test_will_save_rejects_configuration_generation_change_during_formatting(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, configuration=True)
    server.handle(notify("initialized", {}))
    server.drain_server_requests()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    real_format = server._format_nova_document

    def change_configuration_during_format(
        text: str,
        *,
        tab_size: int,
        insert_spaces: bool,
    ) -> str:
        server.handle(
            notify(
                "workspace/didChangeConfiguration",
                {"settings": {"ignored": True}},
            )
        )
        return real_format(
            text,
            tab_size=tab_size,
            insert_spaces=insert_spaces,
        )

    monkeypatch.setattr(
        server,
        "_format_nova_document",
        change_configuration_during_format,
    )

    assert will_save(server, uri, 70) == {
        "jsonrpc": "2.0",
        "id": 70,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_will_save_rejects_workspace_scope_change_during_formatting(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        configuration=True,
        workspace_folders=[{"uri": "file:///workspace/a", "name": "a"}],
    )
    server.handle(notify("initialized", {}))
    server.drain_server_requests()
    uri = "file:///workspace/a/main.nova"
    open_nova(server, uri, "fn main() {\nreturn 1\n}\n")
    real_format = server._format_nova_document

    def change_workspace_during_format(
        text: str,
        *,
        tab_size: int,
        insert_spaces: bool,
    ) -> str:
        server.handle(
            notify(
                "workspace/didChangeWorkspaceFolders",
                {
                    "event": {
                        "added": [
                            {"uri": "file:///workspace/b", "name": "b"},
                        ],
                        "removed": [],
                    }
                },
            )
        )
        return real_format(
            text,
            tab_size=tab_size,
            insert_spaces=insert_spaces,
        )

    monkeypatch.setattr(
        server,
        "_format_nova_document",
        change_workspace_during_format,
    )

    assert will_save(server, uri, 71) == {
        "jsonrpc": "2.0",
        "id": 71,
        "error": {"code": -32801, "message": "Content modified"},
    }
