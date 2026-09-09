from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeAction": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["codeActionProvider"] is True
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


def code_action(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    line: int,
    start: int,
    end: int,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": line, "character": start},
                    "end": {"line": line, "character": end},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_missing_return_quick_fixes_insert_bounded_default_literals() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"

    for request_id, (expected, replacement) in enumerate(
        [("Int", "0"), ("String", '""'), ("Bool", "false")], start=2
    ):
        text = f"fn target() -> {expected} {{}}\n"
        if request_id > 2:
            server.handle(
                notify("textDocument/didClose", {"textDocument": {"uri": uri}})
            )
        open_nova(server, uri, text, version=1)
        type_start = text.index(expected)

        actions = code_action(
            server, uri, request_id, 0, type_start, type_start + len(expected)
        )["result"]
        assert len(actions) == 1
        action = actions[0]
        assert action["title"] == f"Add {expected} return"
        edit = action["edit"]["changes"][uri][0]
        closing = text.index("}")
        assert edit["range"] == {
            "start": {"line": 0, "character": closing},
            "end": {"line": 0, "character": closing},
        }
        assert edit["newText"] == f" return {replacement}; "


def test_nested_conditional_return_still_offers_top_level_return_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target(flag: Bool) -> Int { if (flag) { return 1; } }\n"
    open_nova(server, uri, text)
    type_start = text.index("Int")

    actions = code_action(server, uri, 2, 0, type_start, type_start + 3)["result"]
    assert len(actions) == 1
    action = actions[0]
    assert action["title"] == "Add Int return"
    edit = action["edit"]["changes"][uri][0]
    assert edit["newText"] == "return 0;"
    closing = text.rindex("}")
    assert edit["range"] == {
        "start": {"line": 0, "character": closing},
        "end": {"line": 0, "character": closing},
    }


def test_missing_return_quick_fix_preserves_multiline_closing_indent() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target() -> Int {\n    let value = 1\n}\n"
    open_nova(server, uri, text)
    type_start = text.index("Int")

    actions = code_action(server, uri, 2, 0, type_start, type_start + 3)["result"]
    assert len(actions) == 1
    edit = actions[0]["edit"]["changes"][uri][0]
    assert edit["range"] == {
        "start": {"line": 2, "character": 0},
        "end": {"line": 2, "character": 0},
    }
    assert edit["newText"] == "    return 0;\n"


def test_missing_return_quick_fix_tracks_change_and_close_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    missing = "fn target() -> Int {}\n"
    open_nova(server, uri, missing)
    type_start = missing.index("Int")
    assert code_action(server, uri, 2, 0, type_start, type_start + 3)["result"]

    fixed = "fn target() -> Int { return 1 }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": fixed}],
            },
        )
    )
    fixed_start = fixed.index("Int")
    assert code_action(server, uri, 3, 0, fixed_start, fixed_start + 3)["result"] == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, missing, version=1)
    assert code_action(server, uri, 4, 0, type_start, type_start + 3)["result"]


def test_same_version_replacement_suppresses_stale_missing_return_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target() -> Int {}\n"
    open_nova(server, uri, text)
    type_start = text.index("Int")

    real_commit = server.diagnostics.commit_if_current
    replaced = False

    def replace_then_commit(snapshot, callback):
        nonlocal replaced
        if not replaced:
            replaced = True
            document = server.documents.get(uri)
            assert document is not None
            old_workspace = server.workspace_symbols.get(uri)
            assert old_workspace is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=old_workspace)
            server._publish_workspace_diagnostics()
        return real_commit(snapshot, callback)

    server.diagnostics.commit_if_current = replace_then_commit  # type: ignore[method-assign]
    assert code_action(server, uri, 2, 0, type_start, type_start + 3) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_missing_return_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target() -> Int {}\n"
    open_nova(server, uri, text)
    type_start = text.index("Int")
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, 2, 0, type_start, type_start + 3) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
