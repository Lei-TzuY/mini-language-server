from __future__ import annotations

from threading import Event, Thread
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
            {"capabilities": {"textDocument": {"signatureHelp": {}}}},
        )
    )
    assert result is not None


def open_nova(server: NovaProductLanguageServer, uri: str, version: int, text: str) -> None:
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


def signature_response(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    marked: str,
) -> dict[str, Any]:
    marker = marked.index("/*cursor*/")
    visible = marked.replace("/*cursor*/", "")
    document = server.documents.get(uri)
    assert document is not None
    assert document.text == visible
    line = visible.count("\n", 0, marker)
    line_start = visible.rfind("\n", 0, marker) + 1
    result = server.handle(
        request(
            "textDocument/signatureHelp",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": marker - line_start},
            },
        )
    )
    assert result is not None
    return result


def signature_label(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    marked: str,
) -> str:
    response = signature_response(server, uri, request_id, marked)
    result = response.get("result")
    assert result is not None
    return result["signatures"][0]["label"]


def test_numeric_intrinsic_signature_help_exposes_implemented_conversions() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    uint_call = (
        "fn main() -> Unit { let value = UInt::from(1/*cursor*/); return (); }\n"
    )
    open_nova(server, uri, 1, uint_call.replace("/*cursor*/", ""))
    response = signature_response(server, uri, 2, uint_call)["result"]
    assert response == {
        "signatures": [
            {
                "label": "fn UInt::from(value: Int) -> UInt",
                "parameters": [{"label": "value: Int"}],
            }
        ],
        "activeSignature": 0,
        "activeParameter": 0,
    }

    int_call = (
        "fn main() -> Unit { let value = Int::from_uint(UInt::MAX/*cursor*/); "
        "return (); }\n"
    )
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": int_call.replace("/*cursor*/", "")}],
            },
        )
    )
    assert signature_label(server, uri, 3, int_call) == (
        "fn Int::from_uint(value: UInt) -> Int"
    )


def test_numeric_intrinsic_signature_help_prefers_innermost_call() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn target(value: Int) -> Int { value } "
        "fn main() -> Unit { let value = UInt::from(target(1/*cursor*/)); return (); }\n"
    )
    open_nova(server, uri, 1, text.replace("/*cursor*/", ""))
    assert signature_label(server, uri, 2, text) == "fn target(value: Int) -> Int"

    nested = (
        "fn target(value: UInt) -> UInt { value } "
        "fn main() -> Unit { let value = target(UInt::from(1/*cursor*/)); return (); }\n"
    )
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": nested.replace("/*cursor*/", "")}],
            },
        )
    )
    assert signature_label(server, uri, 3, nested) == (
        "fn UInt::from(value: Int) -> UInt"
    )


def test_numeric_intrinsic_signature_help_close_reopen_uses_new_snapshot() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    old = "fn main() { UInt::from(1/*cursor*/) }\n"
    open_nova(server, uri, 1, old.replace("/*cursor*/", ""))
    assert signature_label(server, uri, 2, old) == "fn UInt::from(value: Int) -> UInt"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    new = "fn main() { Int::from_uint(UInt::MAX/*cursor*/) }\n"
    open_nova(server, uri, 1, new.replace("/*cursor*/", ""))
    assert signature_label(server, uri, 3, new) == (
        "fn Int::from_uint(value: UInt) -> Int"
    )


def test_same_version_workspace_replacement_suppresses_stale_intrinsic_signature() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    marked = "fn main() { UInt::from(1/*cursor*/) }\n"
    open_nova(server, uri, 1, marked.replace("/*cursor*/", ""))
    original = server.workspace_symbols.get(uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert signature_response(server, uri, 41, marked) == {
        "jsonrpc": "2.0",
        "id": 41,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_numeric_intrinsic_signature_help_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    marked = "fn main() { UInt::from(1/*cursor*/) }\n"
    open_nova(server, uri, 1, marked.replace("/*cursor*/", ""))
    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.requests.checkpoint
    blocked = False

    def blocked_checkpoint(context):
        nonlocal blocked
        if not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(context)

    server.requests.checkpoint = blocked_checkpoint  # type: ignore[method-assign]
    thread = Thread(
        target=lambda: responses.append(signature_response(server, uri, 42, marked))
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 42}))
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 42,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
