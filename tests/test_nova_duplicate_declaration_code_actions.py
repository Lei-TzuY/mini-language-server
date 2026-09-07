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


def nth_offset(text: str, token: str, occurrence: int) -> int:
    offset = -1
    for _ in range(occurrence):
        offset = text.index(token, offset + 1)
    return offset


def test_duplicate_function_quick_fix_avoids_existing_suffixes() -> None:
    server = initialized_server()
    uri = "file:///workspace/functions.nova"
    text = "fn ping() {} fn ping() {} fn ping_2() {}\n"
    open_nova(server, uri, text)
    start = nth_offset(text, "ping", 2)

    result = code_action(server, uri, 2, start, start + len("ping"))
    assert len(result["result"]) == 1
    action = result["result"][0]
    assert action["title"] == "Rename duplicate function 'ping' to 'ping_3'"
    edit = action["edit"]["changes"][uri][0]
    assert edit["newText"] == "ping_3"
    assert edit["range"] == {
        "start": {"line": 0, "character": start},
        "end": {"line": 0, "character": start + len("ping")},
    }


def test_duplicate_parameter_quick_fix_is_scope_aware() -> None:
    server = initialized_server()
    uri = "file:///workspace/parameters.nova"
    text = "fn main(value: Int, value: String, value_2: Bool) {}\n"
    open_nova(server, uri, text)
    start = nth_offset(text, "value", 2)

    result = code_action(server, uri, 2, start, start + len("value"))
    assert len(result["result"]) == 1
    action = result["result"][0]
    assert action["title"] == "Rename duplicate parameter 'value' to 'value_3'"
    assert action["edit"]["changes"][uri][0]["newText"] == "value_3"


def test_duplicate_local_quick_fix_avoids_parameter_and_local_collisions() -> None:
    server = initialized_server()
    uri = "file:///workspace/locals.nova"
    text = (
        "fn main(value_2) { let value = value_2 let value = value_2 "
        "let value_3 = value_2 }\n"
    )
    open_nova(server, uri, text)
    start = nth_offset(text, "value", 4)

    result = code_action(server, uri, 2, start, start + len("value"))
    assert len(result["result"]) == 1
    action = result["result"][0]
    assert action["title"] == "Rename duplicate local 'value' to 'value_4'"
    assert action["edit"]["changes"][uri][0]["newText"] == "value_4"


def test_did_change_removes_duplicate_quick_fix_with_old_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/change.nova"
    old = "fn main() { let value = 1 let value = 2 }\n"
    open_nova(server, uri, old)
    old_start = nth_offset(old, "value", 2)
    assert code_action(server, uri, 2, old_start, old_start + 5)["result"]

    new = "fn main() { let value = 1 let item = 2 }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": new}],
            },
        )
    )
    item = new.index("item")
    assert code_action(server, uri, 3, item, item + 4)["result"] == []


def test_close_reopen_rebuilds_duplicate_quick_fix_from_new_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/reopen.nova"
    duplicate = "fn main() { let value = 1 let value = 2 }\n"
    open_nova(server, uri, duplicate)
    start = nth_offset(duplicate, "value", 2)
    assert code_action(server, uri, 2, start, start + 5)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    clean = "fn main() { let value = 1 let item = 2 }\n"
    open_nova(server, uri, clean, version=1)
    item = clean.index("item")
    assert code_action(server, uri, 3, item, item + 4)["result"] == []


def test_same_version_replacement_suppresses_stale_duplicate_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/stale.nova"
    text = "fn main() { let value = 1 let value = 2 }\n"
    open_nova(server, uri, text)
    start = nth_offset(text, "value", 2)

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


def test_duplicate_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/cancel.nova"
    text = "fn main() { let value = 1 let value = 2 }\n"
    open_nova(server, uri, text)
    start = nth_offset(text, "value", 2)
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
