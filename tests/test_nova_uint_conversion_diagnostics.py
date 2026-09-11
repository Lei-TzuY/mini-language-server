from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server(*, pull: bool = False) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    capabilities = {"textDocument": {"diagnostic": {}}} if pull else {}
    response = server.handle(request("initialize", 1, {"capabilities": capabilities}))
    assert response is not None
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


def diagnostics(server: NovaProductLanguageServer, uri: str, code: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == code]


def test_invalid_numeric_conversions_report_exact_type_and_arity_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() -> Unit {\n"
        "  let a = UInt::from(UInt::MAX);\n"
        "  let b = Int::from_uint(1);\n"
        "  let c = UInt::from(1, 2);\n"
        "  return ();\n"
        "}\n"
    )
    open_nova(server, uri, text)

    type_items = diagnostics(server, uri, "nova.conversion-type")
    assert [item.message for item in type_items] == [
        "argument to 'UInt::from' has type 'UInt'; expected 'Int'",
        "argument to 'Int::from_uint' has type 'Int'; expected 'UInt'",
    ]
    assert [text[item.span.start : item.span.end] for item in type_items] == [
        "UInt::MAX",
        "1",
    ]

    arity_items = diagnostics(server, uri, "nova.conversion-arity")
    assert len(arity_items) == 1
    assert arity_items[0].message == "'UInt::from' expects 1 argument; got 2"
    assert text[arity_items[0].span.start : arity_items[0].span.end] == (
        "UInt::from(1, 2)"
    )


def test_valid_and_unknown_conversion_operands_remain_non_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn source() -> Int { return 1; }\n"
        "fn main() -> Unit {\n"
        "  let a = UInt::from(source());\n"
        "  let b = Int::from_uint(UInt::MAX);\n"
        "  let c = UInt::from(unknown_value);\n"
        "  return ();\n"
        "}\n",
    )

    assert diagnostics(server, uri, "nova.conversion-type") == []
    assert diagnostics(server, uri, "nova.conversion-arity") == []


def test_conversion_diagnostics_rebind_after_did_change() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main() -> Unit { let value = UInt::from(UInt::MAX); return (); }\n",
    )
    original = server.diagnostics.get(uri)
    assert original is not None
    assert len(diagnostics(server, uri, "nova.conversion-type")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "text": (
                            "fn main() -> Unit { "
                            "let value = UInt::from(1); return (); }\n"
                        )
                    }
                ],
            },
        )
    )

    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert diagnostics(server, uri, "nova.conversion-type") == []


def test_conversion_diagnostics_close_reopen_use_new_snapshot_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main() -> Unit { let value = Int::from_uint(1); return (); }\n",
    )
    original = server.diagnostics.get(uri)
    assert original is not None
    assert len(diagnostics(server, uri, "nova.conversion-type")) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None

    open_nova(
        server,
        uri,
        "fn main() -> Unit { let value = Int::from_uint(UInt::MAX); return (); }\n",
        version=1,
    )
    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert diagnostics(server, uri, "nova.conversion-type") == []


def test_pull_diagnostics_expose_conversion_errors_with_stable_result_ids() -> None:
    server = initialized_server(pull=True)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main() -> Unit { let value = UInt::from(UInt::MAX); return (); }\n",
    )

    response = server.handle(
        request("textDocument/diagnostic", 2, {"textDocument": {"uri": uri}})
    )
    assert response is not None
    report = response["result"]
    assert report["kind"] == "full"
    conversion_items = [
        item for item in report["items"] if item["code"] == "nova.conversion-type"
    ]
    assert len(conversion_items) == 1
    assert conversion_items[0]["message"] == (
        "argument to 'UInt::from' has type 'UInt'; expected 'Int'"
    )

    unchanged = server.handle(
        request(
            "textDocument/diagnostic",
            3,
            {
                "textDocument": {"uri": uri},
                "previousResultId": report["resultId"],
            },
        )
    )
    assert unchanged is not None
    assert unchanged["result"] == {
        "kind": "unchanged",
        "resultId": report["resultId"],
    }


def test_same_version_workspace_replacement_suppresses_stale_conversion_diagnostics() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn source() -> UInt { return UInt::MAX; }\n")
    open_nova(
        server,
        main_uri,
        "fn main() -> Unit { let value = UInt::from(source()); return (); }\n",
    )
    original_helper = server.workspace_symbols.get(helper_uri)
    original_diagnostics = server.diagnostics.get(main_uri)
    assert original_helper is not None
    assert original_diagnostics is not None
    assert len(diagnostics(server, main_uri, "nova.conversion-type")) == 1

    real_commit = server.workspace_symbols.commit_snapshots_if_current
    replaced = False

    def replace_then_commit(snapshots, callback):
        nonlocal replaced
        if not replaced:
            replaced = True
            document = server.documents.get(helper_uri)
            assert document is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original_helper)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    server._publish_workspace_diagnostics()

    current_helper = server.workspace_symbols.get(helper_uri)
    assert current_helper is not None and current_helper is not original_helper
    assert server.diagnostics.get(main_uri) is original_diagnostics
