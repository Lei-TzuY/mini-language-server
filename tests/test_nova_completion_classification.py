from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer, Span


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None


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


def completion(
    server: NovaProductLanguageServer, uri: str, request_id: int, line: int, character: int
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
    )
    assert response is not None
    return response


def by_label(response: dict[str, Any], label: str) -> dict[str, Any]:
    return next(item for item in response["result"] if item["label"] == label)


def test_completion_items_expose_standard_kinds_and_role_ranking() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn helper(value: Int) -> Int { return value; }\n"
            "fn main(input: Int) -> Unit {\n"
            "  let local = input;\n"
            "  loc\n"
            "}\n"
        ),
    )

    response = completion(server, uri, 2, 3, 2)
    parameter = by_label(response, "input")
    local = by_label(response, "local")
    function = by_label(response, "helper")

    assert parameter["kind"] == 6
    assert local["kind"] == 6
    assert function["kind"] == 3
    assert parameter["sortText"] == "0:input"
    assert local["sortText"] == "0:local"
    assert function["sortText"] == "2:helper"


def test_intrinsic_constants_rank_before_intrinsic_functions() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit {\n  UInt::\n}\n")

    response = completion(server, uri, 3, 1, 8)
    minimum = by_label(response, "MIN")
    conversion = by_label(response, "from")

    assert minimum["kind"] == 21
    assert minimum["sortText"] == "1:MIN"
    assert conversion["kind"] == 3
    assert conversion["sortText"] == "2:from"


def test_classification_composes_with_insert_replace_and_snippets() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "completion": {
                            "completionItem": {
                                "insertReplaceSupport": True,
                                "snippetSupport": True,
                            }
                        }
                    }
                }
            },
        )
    )
    assert initialized is not None
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int) -> Int { return value; }\nfn main() -> Unit {\n  tarfoo;\n}\n",
    )

    item = by_label(completion(server, uri, 4, 2, 5), "target")
    assert item["kind"] == 3
    assert item["sortText"] == "2:target"
    assert item["insertTextFormat"] == 2
    assert item["textEdit"]["newText"] == "target(${1:value})$0"


def test_stale_completion_remains_rejected_before_classification() -> None:
    server = NovaProductLanguageServer()
    initialized = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "completion": {
                            "completionItem": {"insertReplaceSupport": True}
                        }
                    }
                }
            },
        )
    )
    assert initialized is not None
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn helper() -> Unit {}\nfn main() -> Unit {\n  hel\n}\n")
    original = server.workspace_symbols.get(uri)
    assert original is not None
    real_span = server._completion_identifier_span

    def replace_then_span(text: str, offset: int) -> Span:
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_span(text, offset)

    server._completion_identifier_span = replace_then_span  # type: ignore[method-assign]
    assert completion(server, uri, 5, 2, 5) == {
        "jsonrpc": "2.0",
        "id": 5,
        "error": {"code": -32801, "message": "Content modified"},
    }
