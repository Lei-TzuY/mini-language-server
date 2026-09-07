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
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": end},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def test_local_type_mismatches_get_deterministic_default_literals() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main() { let count: Int = "bad" '
        "let name: String = true "
        "let ready: Bool = 7 }\n"
    )
    open_nova(server, uri, text)

    for request_id, (expression, expected, replacement) in enumerate(
        [('"bad"', "Int", "0"), ("true", "String", '""'), ("7", "Bool", "false")],
        start=2,
    ):
        start = text.index(expression)
        result = code_action(server, uri, request_id, start, start + len(expression))
        actions = result["result"]
        assert len(actions) == 1
        action = actions[0]
        assert action["title"] == f"Replace local initializer with {expected} literal"
        edit = action["edit"]["changes"][uri][0]
        assert edit["newText"] == replacement
        assert edit["range"]["start"] == {"line": 0, "character": start}
        assert edit["range"]["end"] == {
            "line": 0,
            "character": start + len(expression),
        }


def test_reference_local_type_mismatch_uses_current_diagnostic_and_did_change() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(input: String) { let count: Int = input }\n"
    open_nova(server, uri, text)
    start = text.index("input", text.index("let"))

    result = code_action(server, uri, 2, start, start + len("input"))
    actions = result["result"]
    assert len(actions) == 1
    assert actions[0]["edit"]["changes"][uri][0]["newText"] == "0"

    changed = "fn main(input: Int) { let count: Int = input }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )
    changed_start = changed.index("input", changed.index("let"))
    assert code_action(
        server, uri, 3, changed_start, changed_start + len("input")
    )["result"] == []


def test_close_reopen_rebuilds_local_type_quick_fix_from_new_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = 'fn main() { let count: Int = "bad" }\n'
    open_nova(server, uri, bad)
    start = bad.index('"bad"')
    assert code_action(server, uri, 2, start, start + 5)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    good = "fn main() { let count: Int = 7 }\n"
    open_nova(server, uri, good, version=1)
    current = good.index("7")
    assert code_action(server, uri, 3, current, current + 1)["result"] == []


def test_same_version_replacement_suppresses_stale_local_type_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count: Int = "bad" }\n'
    open_nova(server, uri, text)
    start = text.index('"bad"')

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
    assert code_action(server, uri, 2, start, start + 5) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_local_type_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count: Int = "bad" }\n'
    open_nova(server, uri, text)
    start = text.index('"bad"')
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, 2, start, start + 5) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
