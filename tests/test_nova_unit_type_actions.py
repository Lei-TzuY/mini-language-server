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


def position(text: str, offset: int) -> dict[str, int]:
    prefix = text[:offset]
    line = prefix.count("\n")
    line_start = prefix.rfind("\n") + 1
    return {"line": line, "character": offset - line_start}


def code_action(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    request_id: int,
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
                    "start": position(text, start),
                    "end": position(text, end),
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_unit_mismatches_get_deterministic_unit_literal_quick_fixes() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn consume(value: Unit) -> Unit { return (); }\n"
        "fn bad_return() -> Unit { return 1; }\n"
        "fn main() -> Unit {\n"
        "  consume(1);\n"
        "  let local: Unit = 1;\n"
        "  var target: Unit = ();\n"
        "  target = 1;\n"
        "  return ();\n"
        "}\n"
    )
    open_nova(server, uri, text)

    cases = [
        ("return 1", len("return "), "Replace return expression with Unit literal"),
        ("consume(1)", len("consume("), "Replace argument with Unit literal"),
        ("local: Unit = 1", len("local: Unit = "), "Replace local initializer with Unit literal"),
        ("target = 1", len("target = "), "Replace assignment value with Unit literal"),
    ]
    for request_id, (needle, delta, title) in enumerate(cases, start=2):
        start = text.index(needle) + delta
        response = code_action(server, uri, text, request_id, start, start + 1)
        actions = [action for action in response["result"] if action["title"] == title]
        assert len(actions) == 1
        edit = actions[0]["edit"]["changes"][uri][0]
        assert edit["newText"] == "()"
        assert edit["range"] == {
            "start": position(text, start),
            "end": position(text, start + 1),
        }


def test_did_change_rebinds_unit_quick_fix_to_current_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = "fn main() -> Unit { return 1; }\n"
    open_nova(server, uri, bad)
    start = bad.index("1")
    assert code_action(server, uri, bad, 2, start, start + 1)["result"]

    good = "fn main() -> Unit { return (); }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": good}],
            },
        )
    )
    current = good.index("()")
    assert code_action(server, uri, good, 3, current, current + 2)["result"] == []


def test_close_reopen_rebuilds_unit_quick_fix_from_new_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = "fn main() -> Unit { return 1; }\n"
    open_nova(server, uri, bad)
    start = bad.index("1")
    assert code_action(server, uri, bad, 2, start, start + 1)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, bad, version=1)
    assert code_action(server, uri, bad, 3, start, start + 1)["result"]


def test_same_version_replacement_suppresses_stale_unit_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() -> Unit { return 1; }\n"
    open_nova(server, uri, text)
    start = text.index("1")

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
    assert code_action(server, uri, text, 2, start, start + 1) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_unit_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() -> Unit { return 1; }\n"
    open_nova(server, uri, text)
    start = text.index("1")
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, text, 2, start, start + 1) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
