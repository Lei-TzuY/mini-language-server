from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": {"codeAction": {}}}})
    )
    assert response is not None
    assert response["result"]["capabilities"]["codeActionProvider"] is True
    return server


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, *, version: int = 1
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
    response = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {"start": position(text, start), "end": position(text, end)},
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert response is not None
    return response


def test_redundant_numeric_conversions_get_exact_quick_fixes() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "  let a = UInt::from(UInt::MAX);\n"
        "  let b = Int::from_uint(1);\n"
        "  return ();\n"
        "}\n"
    )
    open_nova(server, uri, text)

    cases = [
        ("UInt::from(UInt::MAX)", "UInt::MAX"),
        ("Int::from_uint(1)", "1"),
    ]
    for request_id, (call, replacement) in enumerate(cases, start=2):
        call_start = text.index(call)
        argument_start = call_start + call.index("(") + 1
        response = code_action(
            server, uri, text, request_id, argument_start, argument_start + len(replacement)
        )
        actions = [
            action
            for action in response["result"]
            if action["title"] == "Remove redundant numeric conversion"
        ]
        assert len(actions) == 1
        edit = actions[0]["edit"]["changes"][uri][0]
        assert edit["newText"] == replacement
        assert edit["range"] == {
            "start": position(text, call_start),
            "end": position(text, call_start + len(call)),
        }


def test_nonredundant_invalid_conversion_stays_without_guessing_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() -> Unit { let value = UInt::from(true); return (); }\n"
    open_nova(server, uri, text)
    start = text.index("true")
    response = code_action(server, uri, text, 2, start, start + len("true"))
    assert [
        action
        for action in response["result"]
        if action["title"] == "Remove redundant numeric conversion"
    ] == []


def test_conversion_quick_fix_rebinds_after_change_and_close_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = "fn main() -> Unit { let value = UInt::from(UInt::MAX); return (); }\n"
    open_nova(server, uri, bad)
    start = bad.index("UInt::MAX")
    assert code_action(server, uri, bad, 2, start, start + len("UInt::MAX"))["result"]

    good = "fn main() -> Unit { let value = UInt::from(1); return (); }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {"textDocument": {"uri": uri, "version": 2}, "contentChanges": [{"text": good}]},
        )
    )
    current = good.index("1")
    assert code_action(server, uri, good, 3, current, current + 1)["result"] == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, bad, version=1)
    assert code_action(server, uri, bad, 4, start, start + len("UInt::MAX"))["result"]


def test_same_version_replacement_suppresses_stale_conversion_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() -> Unit { let value = UInt::from(UInt::MAX); return (); }\n"
    open_nova(server, uri, text)
    start = text.index("UInt::MAX")

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
    assert code_action(server, uri, text, 2, start, start + len("UInt::MAX")) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_conversion_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() -> Unit { let value = UInt::from(UInt::MAX); return (); }\n"
    open_nova(server, uri, text)
    start = text.index("UInt::MAX")
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, text, 2, start, start + len("UInt::MAX")) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
