from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": text,
                }
            },
        )
    )


def completion_details(server: NovaProductLanguageServer, uri: str) -> dict[str, str]:
    document = server.documents.get(uri)
    assert document is not None
    response = server.handle(
        request(
            "textDocument/completion",
            20,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": document.text.rfind("}")},
            },
        )
    )
    assert response is not None
    return {item["label"]: item["detail"] for item in response["result"]}


def test_completion_exposes_unique_same_and_cross_file_function_signatures() -> None:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    main_uri = "file:///workspace/main.nova"
    helper_uri = "file:///workspace/helper.nova"
    open_nova(server, helper_uri, "fn helper(value: Int) -> Int { value }\n")
    open_nova(server, main_uri, "fn main(input: String) { input }\n")

    details = completion_details(server, main_uri)
    assert details["helper"] == "fn helper(value: Int) -> Int"
    assert details["main"] == "fn main(input: String) -> String"
    assert details["input"] == "parameter: String"


def test_completion_keeps_ambiguous_function_detail_conservative() -> None:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn helper(value: Int) { value }\n")
    open_nova(server, "file:///workspace/b.nova", "fn helper(value: String) { value }\n")
    open_nova(server, main_uri, "fn main() { }\n")

    details = completion_details(server, main_uri)
    assert details["helper"] == "function"


def test_completion_surfaces_conservative_inferred_return_types() -> None:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/int.nova", "fn integer() { return 1; }\n")
    open_nova(server, "file:///workspace/string.nova", 'fn text() { return "x"; }\n')
    open_nova(server, main_uri, "fn main() { return true; }\n")

    details = completion_details(server, main_uri)
    assert details["integer"] == "fn integer() -> Int"
    assert details["text"] == "fn text() -> String"
    assert details["main"] == "fn main() -> Bool"


def test_completion_preserves_explicit_and_ambiguous_return_details() -> None:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/explicit.nova", "fn exact() -> Int { return 1; }\n")
    open_nova(server, "file:///workspace/a.nova", "fn duplicate() { return 1; }\n")
    open_nova(server, "file:///workspace/b.nova", "fn duplicate() { return 1; }\n")
    open_nova(server, main_uri, "fn main() { }\n")

    details = completion_details(server, main_uri)
    assert details["exact"] == "fn exact() -> Int"
    assert details["duplicate"] == "function"


def test_inferred_completion_recomputes_across_change_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn helper() { return 1; }\n")
    open_nova(server, main_uri, "fn main() { }\n")
    assert completion_details(server, main_uri)["helper"] == "fn helper() -> Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": 'fn helper() { return "x"; }\n'}],
            },
        )
    )
    assert completion_details(server, main_uri)["helper"] == "fn helper() -> String"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert "helper" not in completion_details(server, main_uri)

    open_nova(server, helper_uri, "fn helper() { return true; }\n")
    assert completion_details(server, main_uri)["helper"] == "fn helper() -> Bool"
