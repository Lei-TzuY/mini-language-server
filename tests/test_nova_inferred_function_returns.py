from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(
        request("initialize", 1, {"capabilities": {"textDocument": {"inlayHint": {}}}})
    ) is not None
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(notify("textDocument/didOpen", {"textDocument": {"uri": uri, "languageId": "nova", "version": 1, "text": text}}))


def hover_value(server: NovaProductLanguageServer, uri: str, text: str) -> str:
    offset = text.rindex("value")
    response = server.handle(request("textDocument/hover", 10, {"textDocument": {"uri": uri}, "position": {"line": 0, "character": offset}}))
    assert response is not None
    return response["result"]["contents"]["value"]


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {diagnostic.code for diagnostic in snapshot.diagnostics if diagnostic.code is not None}


def test_unannotated_literal_return_infers_call_initializer_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn helper() { return 1; } fn main() { let value = helper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value: Int"
    offset = text.rindex("value")
    completion = server.handle(request("textDocument/completion", 11, {"textDocument": {"uri": uri}, "position": {"line": 0, "character": offset}}))
    assert completion is not None
    item = next(item for item in completion["result"] if item["label"] == "value")
    assert item["detail"] == "variable: Int"
    hints = server.handle(request("textDocument/inlayHint", 12, {"textDocument": {"uri": uri}, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": len(text.rstrip("\n"))}}}))
    assert hints is not None
    assert any(hint.get("label") == ": Int" for hint in hints["result"])


def test_parenthesized_literal_and_call_returns_infer_bounded_types() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn source() { return ((1)); } fn wrapper() { return (source()); } fn main() { let value = wrapper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value: Int"


def test_parenthesized_compound_return_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn source() -> Int { return 1; } fn wrapper() { return (source() + 1); } fn main() { let value = wrapper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value"


def test_cross_file_literal_return_inference_recomputes_after_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, helper_uri, 'fn helper() { return "x"; }\n')
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: String"
    server.handle(notify("textDocument/didChange", {"textDocument": {"uri": helper_uri, "version": 2}, "contentChanges": [{"text": "fn helper() { return true; }\n"}]}))
    assert hover_value(server, main_uri, main) == "variable value: Bool"


def test_cross_file_literal_return_inference_rebinds_after_close_reopen() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, helper_uri, "fn helper() { return 1; }\n")
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: Int"
    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert hover_value(server, main_uri, main) == "variable value"
    open_nova(server, helper_uri, 'fn helper() { return "x"; }\n')
    assert hover_value(server, main_uri, main) == "variable value: String"


def test_conflicting_or_unknown_returns_remain_conservative() -> None:
    server = initialized_server()
    conflict_uri = "file:///workspace/conflict.nova"
    conflict = "fn helper() { return 1; return true; } fn main() { let value = helper() value }\n"
    open_nova(server, conflict_uri, conflict)
    assert hover_value(server, conflict_uri, conflict) == "variable value"
    server = initialized_server()
    unknown_uri = "file:///workspace/unknown.nova"
    unknown = "fn helper() { let other = 1 return other; } fn main() { let value = helper() value }\n"
    open_nova(server, unknown_uri, unknown)
    assert hover_value(server, unknown_uri, unknown) == "variable value"


def test_explicit_unsupported_return_annotation_is_not_overridden() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn helper() -> Float { return 1; } fn main() { let value = helper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value"


def test_inferred_function_result_flows_into_argument_and_return_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn source() { return "x"; } fn sink(value: Int) {} fn relay() -> Int { return source(); } fn main() { sink(source()) }\n'
    open_nova(server, uri, text)
    assert {"nova.argument-type", "nova.return-type"} <= diagnostic_codes(server, uri)


def test_ambiguous_inferred_function_result_is_suppressed() -> None:
    server = initialized_server()
    main_uri = "file:///workspace/main.nova"
    main = "fn main() { let value = helper() value }\n"
    open_nova(server, "file:///workspace/a.nova", "fn helper() { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", "fn helper() { return 1; }\n")
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value"


def test_unannotated_wrapper_infers_explicit_direct_call_return_type() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn source() -> Int { return 1; } fn wrapper() { return source(); } fn main() { let value = wrapper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value: Int"


def test_cross_file_wrapper_inference_recomputes_after_explicit_target_change() -> None:
    server = initialized_server()
    source_uri = "file:///workspace/source.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    wrapper = "fn wrapper() { return source(); }\n"
    main = "fn main() { let value = wrapper() value }\n"
    open_nova(server, source_uri, "fn source() -> Int { return 1; }\n")
    open_nova(server, wrapper_uri, wrapper)
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value: Int"
    server.handle(notify("textDocument/didChange", {"textDocument": {"uri": source_uri, "version": 2}, "contentChanges": [{"text": "fn source() -> Bool { return true; }\n"}]}))
    assert hover_value(server, main_uri, main) == "variable value: Bool"


def test_wrapper_inference_rejects_ambiguous_explicit_target() -> None:
    server = initialized_server()
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn source() -> Int { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", "fn source() -> Int { return 1; }\n")
    open_nova(server, wrapper_uri, "fn wrapper() { return source(); }\n")
    main = "fn main() { let value = wrapper() value }\n"
    open_nova(server, main_uri, main)
    assert hover_value(server, main_uri, main) == "variable value"


def test_wrapper_inference_requires_consistent_literal_and_call_returns() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn source() -> Int { return 1; } fn wrapper() { return source(); return true; } fn main() { let value = wrapper() value }\n"
    open_nova(server, uri, text)
    assert hover_value(server, uri, text) == "variable value"
