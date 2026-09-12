from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeLens": {}}}},
        )
    )
    assert result is not None
    capabilities = result["result"]["capabilities"]
    assert capabilities["codeLensProvider"] == {"resolveProvider": True}
    assert capabilities["executeCommandProvider"] == {
        "commands": ["mini-language-server.showReferences"]
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


def lenses(
    server: NovaProductLanguageServer, uri: str, request_id: int = 2
) -> dict[str, Any]:
    result = server.handle(
        request("textDocument/codeLens", request_id, {"textDocument": {"uri": uri}})
    )
    assert result is not None
    return result


def resolve_lens(
    server: NovaProductLanguageServer,
    lens: dict[str, Any],
    request_id: int,
) -> dict[str, Any]:
    result = server.handle(request("codeLens/resolve", request_id, lens))
    assert result is not None
    return result


def resolved_title(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    lens_request_id: int,
    resolve_request_id: int,
) -> str:
    lens = lenses(server, uri, lens_request_id)["result"][0]
    resolved = resolve_lens(server, lens, resolve_request_id)["result"]
    return resolved["command"]["title"]


def test_reference_code_lens_resolves_cross_file_count_and_executes_locations() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target(value: Int) -> Int { value }\n")
    open_nova(
        server,
        caller_uri,
        "fn caller() {\n  target(1)\n  target(2)\n}\n",
    )

    result = lenses(server, library_uri)["result"]
    assert len(result) == 1
    assert "command" not in result[0]
    assert result[0]["data"]["uri"] == library_uri
    assert result[0]["data"]["name"] == "target"
    assert isinstance(result[0]["data"]["novaCodeLensResolve"], int)

    resolved = resolve_lens(server, result[0], 3)["result"]
    assert resolved["command"] == {
        "title": "2 references",
        "command": "mini-language-server.showReferences",
        "arguments": [{"uri": library_uri, "name": "target"}],
    }

    locations = server.handle(
        request(
            "workspace/executeCommand",
            4,
            {
                "command": resolved["command"]["command"],
                "arguments": resolved["command"]["arguments"],
            },
        )
    )
    assert locations is not None
    assert locations["result"] == [
        {
            "uri": caller_uri,
            "range": {
                "start": {"line": 1, "character": 2},
                "end": {"line": 1, "character": 8},
            },
        },
        {
            "uri": caller_uri,
            "range": {
                "start": {"line": 2, "character": 2},
                "end": {"line": 2, "character": 8},
            },
        },
    ]


def test_reference_code_lens_refuses_ambiguity_and_tracks_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    duplicate_uri = "file:///workspace/duplicate.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert resolved_title(
        server, target_uri, lens_request_id=2, resolve_request_id=3
    ) == "1 reference"

    open_nova(server, duplicate_uri, "fn target() {}\n")
    assert lenses(server, target_uri, 4)["result"] == []

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": duplicate_uri}})
    )
    assert resolved_title(
        server, target_uri, lens_request_id=5, resolve_request_id=6
    ) == "1 reference"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": caller_uri}}))
    assert resolved_title(
        server, target_uri, lens_request_id=7, resolve_request_id=8
    ) == "0 references"

    open_nova(server, caller_uri, "fn caller() { target() target() }\n")
    assert resolved_title(
        server, target_uri, lens_request_id=9, resolve_request_id=10
    ) == "2 references"


def test_reference_code_lens_tracks_incremental_workspace_changes() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert resolved_title(
        server, target_uri, lens_request_id=2, resolve_request_id=3
    ) == "1 reference"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": caller_uri, "version": 2},
                "contentChanges": [{"text": "fn caller() { target() target() target() }\n"}],
            },
        )
    )
    assert resolved_title(
        server, target_uri, lens_request_id=4, resolve_request_id=5
    ) == "3 references"


def test_reference_code_lens_suppresses_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    original = server.workspace_symbols.get(caller_uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(caller_uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert lenses(server, target_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_reference_code_lens_honors_cancellation_before_publication() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def cancel_on_second_checkpoint(context):
        nonlocal calls
        calls += 1
        if calls == 2:
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_on_second_checkpoint  # type: ignore[method-assign]
    assert lenses(server, target_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }


def test_code_lens_resolve_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    lens = lenses(server, target_uri)["result"][0]

    original = server.workspace_symbols.get(caller_uri)
    assert original is not None
    document = server.documents.get(caller_uri)
    assert document is not None
    replacement = server.nova_adapter.publish(server, document)
    server.workspace_symbols.replace(replacement, expected=original)

    assert resolve_lens(server, lens, 3) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_code_lens_resolve_rejects_close_reopen_same_version() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    lens = lenses(server, target_uri)["result"][0]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": target_uri}}))
    open_nova(server, target_uri, "fn target() {}\n", version=1)

    assert resolve_lens(server, lens, 3) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_code_lens_resolve_honors_cancellation_before_publication() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    lens = lenses(server, target_uri)["result"][0]
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def cancel_on_second_checkpoint(context):
        nonlocal calls
        calls += 1
        if calls == 2:
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_on_second_checkpoint  # type: ignore[method-assign]
    assert resolve_lens(server, lens, 3) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
