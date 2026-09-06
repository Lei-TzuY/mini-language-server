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
    assert details["main"] == "fn main(input: String)"
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
