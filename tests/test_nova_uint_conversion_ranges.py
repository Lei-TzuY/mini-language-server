from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, version: int = 1
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


def range_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        item for item in snapshot.diagnostics if item.code == "nova.conversion-range"
    ]


def test_conversion_range_diagnostics_cover_provable_checked_failures() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "  let a = UInt::from(-1);\n"
        "  let b = UInt::from((3 - 5));\n"
        "  let c = Int::from_uint((UInt::MAX));\n"
        "  return ();\n"
        "}\n"
    )
    open_nova(server, uri, text)

    diagnostics = range_diagnostics(server, uri)
    assert len(diagnostics) == 3
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        "-1",
        "(3 - 5)",
        "(UInt::MAX)",
    ]
    assert "constant Int value -1" in diagnostics[0].message
    assert "constant Int value -2" in diagnostics[1].message
    assert "UInt::MAX" in diagnostics[2].message


def test_conversion_range_diagnostics_stay_fail_closed_for_safe_or_unknown_values() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn source() -> Int { return 1; }\n"
        "fn main() -> Unit {\n"
        "  let a = UInt::from(0);\n"
        "  let b = UInt::from(1 + 2);\n"
        "  let c = UInt::from(source());\n"
        "  let d = Int::from_uint(UInt::MIN);\n"
        "  let e = Int::from_uint(UInt::from(7));\n"
        "  return ();\n"
        "}\n"
    )
    open_nova(server, uri, text)

    assert range_diagnostics(server, uri) == []


def test_conversion_range_diagnostics_track_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main() -> UInt { return UInt::from(1 - 2); }\n"
    valid = "fn main() -> UInt { return UInt::from(2 - 1); }\n"
    open_nova(server, uri, invalid, 1)
    assert len(range_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert range_diagnostics(server, uri) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, "fn main() -> Int { return Int::from_uint(UInt::MAX); }\n", 1)
    assert len(range_diagnostics(server, uri)) == 1


def test_stale_semantic_cannot_replace_newer_conversion_range_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() -> UInt { return UInt::from(-1); }\n"
    open_nova(server, uri, text, 1)

    original = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert original is not None
    assert document is not None
    stale_semantic = original.semantic

    replacement_semantic = server.nova_adapter.publish(server, document)
    current = server.diagnostics.get(uri)
    assert current is not None
    assert current.semantic is replacement_semantic
    assert current is not original

    assert server.publish_diagnostics(stale_semantic, ()) is False
    assert server.diagnostics.get(uri) is current
    assert len(range_diagnostics(server, uri)) == 1
