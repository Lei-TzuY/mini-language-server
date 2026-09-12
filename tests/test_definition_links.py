from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer, *, link_support: bool
) -> dict[str, Any]:
    capabilities: dict[str, Any] = {}
    if link_support:
        capabilities = {"textDocument": {"definition": {"linkSupport": True}}}
    result = server.handle(request("initialize", 1, {"capabilities": capabilities}))
    assert result is not None
    return result


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
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
    request_id: int = 2,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/definition",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 16},
            },
        )
    )
    assert result is not None
    return result


def test_definition_link_support_returns_precise_cross_file_link() -> None:
    server = NovaProductLanguageServer()
    initialize(server, link_support=True)
    target_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert definition(server, caller_uri)["result"] == [
        {
            "originSelectionRange": {
                "start": {"line": 0, "character": 14},
                "end": {"line": 0, "character": 20},
            },
            "targetUri": target_uri,
            "targetRange": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 14},
            },
            "targetSelectionRange": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 9},
            },
        }
    ]


def test_definition_without_link_support_preserves_location_shape() -> None:
    server = NovaProductLanguageServer()
    initialize(server, link_support=False)
    target_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert definition(server, caller_uri)["result"] == {
        "uri": target_uri,
        "range": {
            "start": {"line": 0, "character": 3},
            "end": {"line": 0, "character": 9},
        },
    }


def test_definition_links_track_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server, link_support=True)
    target_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert definition(server, caller_uri)["result"] is not None

    close = {"textDocument": {"uri": target_uri}}
    server.handle(notify("textDocument/didClose", close))
    assert definition(server, caller_uri, 3)["result"] is None

    open_nova(server, target_uri, "fn target() {}\n", version=1)
    reopened = definition(server, caller_uri, 4)["result"]
    assert isinstance(reopened, list)
    assert reopened[0]["targetUri"] == target_uri


def test_definition_link_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server, link_support=True)
    target_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    original = server.workspace_symbols.get(caller_uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current
    commit_count = 0

    def replace_on_second_commit(snapshots, callback):
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            document = server.documents.get(caller_uri)
            assert document is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = (  # type: ignore[method-assign]
        replace_on_second_commit
    )

    assert definition(server, caller_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_definition_link_honors_underlying_request_cancellation() -> None:
    server = NovaProductLanguageServer()
    initialize(server, link_support=True)
    target_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    real_checkpoint = server.requests.checkpoint
    checkpoints = 0

    def cancel_before_second_checkpoint(context) -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            server.handle(notify("$/cancelRequest", {"id": 9}))
        real_checkpoint(context)

    server.requests.checkpoint = cancel_before_second_checkpoint  # type: ignore[method-assign]

    assert definition(server, caller_uri, 9) == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
