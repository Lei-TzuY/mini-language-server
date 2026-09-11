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


def test_uint_constants_participate_in_argument_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn consume(value: UInt) -> Unit { return (); }\n"
        "fn main() -> Unit { consume(UInt::MIN); consume(UInt::MAX); return (); }\n"
    )
    open_nova(server, uri, text)

    assert diagnostics(server, uri, "nova.argument-type") == []
    assert diagnostics(server, uri, "nova.unresolved-name") == []


def test_uint_constant_reports_argument_and_return_mismatches() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn consume(value: Int) -> Unit { return (); }\n"
        "fn main() -> Int { consume(UInt::MAX); return UInt::MIN; }\n"
    )
    open_nova(server, uri, text)

    arguments = diagnostics(server, uri, "nova.argument-type")
    assert len(arguments) == 1
    assert arguments[0].message == "argument 1 to 'consume' has type 'UInt'; expected 'Int'"
    assert text[arguments[0].span.start : arguments[0].span.end] == "UInt::MAX"

    returns = diagnostics(server, uri, "nova.return-type")
    assert len(returns) == 1
    assert returns[0].message == "return type mismatch: expected 'Int', got 'UInt'"
    assert text[returns[0].span.start : returns[0].span.end] == "UInt::MIN"


def test_uint_return_annotation_requires_a_value_and_accepts_uint_tail() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn ok() -> UInt { UInt::MAX }\n"
        "fn missing() -> UInt { }\n",
    )

    assert diagnostics(server, uri, "nova.return-type") == []
    missing = diagnostics(server, uri, "nova.missing-return")
    assert len(missing) == 1
    assert missing[0].message == "function 'missing' with return type 'UInt' has no value return"


def test_uint_constant_participates_in_local_and_assignment_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn main() -> Unit {\n"
        "  let ok: UInt = UInt::MIN;\n"
        "  let bad: UInt = 1;\n"
        "  var target: UInt = UInt::MAX;\n"
        "  target = 1;\n"
        "  return ();\n"
        "}\n",
    )

    locals_ = diagnostics(server, uri, "nova.local-type")
    assert len(locals_) == 1
    assert locals_[0].message == "local type mismatch: expected 'UInt', got 'Int'"

    assignments = diagnostics(server, uri, "nova.assignment-type")
    assert len(assignments) == 1
    assert assignments[0].message == "assignment type mismatch: expected 'UInt', got 'Int'"


def test_explicit_uint_call_result_propagates_across_workspace() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn source() -> UInt { return UInt::MAX; }\n")
    main_text = "fn main() -> Int { return source(); }\n"
    open_nova(server, main_uri, main_text)

    returns = diagnostics(server, main_uri, "nova.return-type")
    assert len(returns) == 1
    assert returns[0].message == "return type mismatch: expected 'Int', got 'UInt'"
    assert main_text[returns[0].span.start : returns[0].span.end] == "source()"


def test_unannotated_uint_result_inference_reuses_exact_constant_typing() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn source() { return UInt::MAX; }\n"
        "fn consume(value: UInt) -> Unit { return (); }\n"
        "fn main() -> Unit { consume(source()); return (); }\n",
    )

    assert diagnostics(server, uri, "nova.argument-type") == []


def test_cross_file_uint_result_rebinds_after_did_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn source() -> UInt { return UInt::MAX; }\n")
    open_nova(
        server,
        main_uri,
        "fn consume(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit { consume(source()); return (); }\n",
    )
    assert len(diagnostics(server, main_uri, "nova.argument-type")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn source() -> Int { return 1; }\n"}],
            },
        )
    )

    assert diagnostics(server, main_uri, "nova.argument-type") == []


def test_uint_constant_close_reopen_uses_new_snapshot_identity() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn consume(value: Int) -> Unit { return (); }\n"
        "fn main() -> Unit { consume(UInt::MAX); return (); }\n",
    )
    original = server.diagnostics.get(uri)
    assert original is not None
    assert len(diagnostics(server, uri, "nova.argument-type")) == 1

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None

    open_nova(
        server,
        uri,
        "fn consume(value: UInt) -> Unit { return (); }\n"
        "fn main() -> Unit { consume(UInt::MAX); return (); }\n",
        version=1,
    )
    current = server.diagnostics.get(uri)
    assert current is not None and current is not original
    assert diagnostics(server, uri, "nova.argument-type") == []


def test_same_version_workspace_replacement_suppresses_stale_uint_call_result() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn source() -> UInt { return UInt::MAX; }\n")
    open_nova(server, main_uri, "fn main() -> Int { return source(); }\n")
    original_helper = server.workspace_symbols.get(helper_uri)
    original_diagnostics = server.diagnostics.get(main_uri)
    assert original_helper is not None
    assert original_diagnostics is not None
    assert len(diagnostics(server, main_uri, "nova.return-type")) == 1

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
