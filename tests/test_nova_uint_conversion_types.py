from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
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


def test_explicit_numeric_conversions_participate_in_argument_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn take_uint(value: UInt) -> Unit { return (); }\n"
        "fn take_int(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit {\n"
        "  take_uint(UInt::from(1 + 2));\n"
        "  take_int(Int::from_uint(UInt::MAX - UInt::MIN));\n"
        "  take_int(Int::from_uint(UInt::from(7)));\n"
        "  return ();\n"
        "}\n",
    )

    assert diagnostics(server, uri, "nova.argument-type") == []
    assert diagnostics(server, uri, "nova.unresolved-name") == []


def test_wrong_source_family_and_arity_stay_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn take_int(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit {\n"
        "  take_int(UInt::from(UInt::MAX));\n"
        "  take_int(Int::from_uint(1));\n"
        "  take_int(UInt::from(1, 2));\n"
        "  return ();\n"
        "}\n",
    )

    assert diagnostics(server, uri, "nova.argument-type") == []


def test_conversion_results_feed_unannotated_function_inference() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn as_uint() { return UInt::from(1); }\n"
        "fn as_int() { return Int::from_uint(UInt::MAX); }\n"
        "fn take_int(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit { take_int(as_uint()); take_int(as_int()); return (); }\n"
    )
    open_nova(server, uri, text)

    items = diagnostics(server, uri, "nova.argument-type")
    assert len(items) == 1
    assert items[0].message == "argument 1 to 'take_int' has type 'UInt'; expected 'Int'"
    assert text[items[0].span.start : items[0].span.end] == "as_uint()"


def test_cross_file_conversion_rebinds_after_source_result_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn source() -> Int { return 1; }\n")
    open_nova(
        server,
        main_uri,
        "fn take_int(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit { take_int(UInt::from(source())); return (); }\n",
    )
    assert len(diagnostics(server, main_uri, "nova.argument-type")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn source() -> UInt { return UInt::MAX; }\n"}
                ],
            },
        )
    )

    assert diagnostics(server, main_uri, "nova.argument-type") == []


def test_conversion_close_reopen_uses_new_snapshot_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn take_int(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit { take_int(UInt::from(1)); return (); }\n",
    )
    original = server.diagnostics.get(uri)
    assert original is not None
    assert len(diagnostics(server, uri, "nova.argument-type")) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None

    open_nova(
        server,
        uri,
        "fn take_uint(value: UInt) -> Unit { return (); }\n"
        "fn main() -> Unit { take_uint(UInt::from(1)); return (); }\n",
        version=1,
    )
    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert diagnostics(server, uri, "nova.argument-type") == []


def test_same_version_workspace_replacement_suppresses_stale_conversion_result() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn source() -> Int { return 1; }\n")
    open_nova(
        server,
        main_uri,
        "fn take_int(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit { take_int(UInt::from(source())); return (); }\n",
    )
    original_helper = server.workspace_symbols.get(helper_uri)
    original_diagnostics = server.diagnostics.get(main_uri)
    assert original_helper is not None
    assert original_diagnostics is not None
    assert len(diagnostics(server, main_uri, "nova.argument-type")) == 1

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
