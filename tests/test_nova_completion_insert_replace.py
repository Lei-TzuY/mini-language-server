from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer, Span


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer, *, insert_replace: bool, snippets: bool = False
) -> None:
    assert server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "completion": {
                            "completionItem": {
                                "insertReplaceSupport": insert_replace,
                                "snippetSupport": snippets,
                            }
                        }
                    }
                }
            },
        )
    )


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
    server: NovaProductLanguageServer, uri: str, request_id: int, character: int
) -> dict:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": character},
            },
        )
    )
    assert response is not None
    return response


def by_label(response: dict, label: str) -> dict:
    return next(item for item in response["result"] if item["label"] == label)


def source_text() -> str:
    return (
        "fn target(value: Int) -> Int { return value; }\n"
        "fn main() -> Unit {\n"
        "  tarfoo;\n"
        "}\n"
    )


def test_completion_insert_replace_edit_replaces_identifier_suffix() -> None:
    server = NovaProductLanguageServer()
    initialize(server, insert_replace=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    item = by_label(completion(server, uri, 2, 5), "target")
    assert item["textEdit"] == {
        "newText": "target",
        "insert": {
            "start": {"line": 2, "character": 2},
            "end": {"line": 2, "character": 5},
        },
        "replace": {
            "start": {"line": 2, "character": 2},
            "end": {"line": 2, "character": 8},
        },
    }
    assert "insertText" not in item


def test_completion_insert_replace_preserves_function_snippet_new_text() -> None:
    server = NovaProductLanguageServer()
    initialize(server, insert_replace=True, snippets=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    item = by_label(completion(server, uri, 2, 5), "target")
    assert item["textEdit"]["newText"] == "target(${1:value})$0"
    assert item["insertTextFormat"] == 2
    assert "insertText" not in item


def test_client_without_insert_replace_support_keeps_existing_completion_shape() -> None:
    server = NovaProductLanguageServer()
    initialize(server, insert_replace=False, snippets=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    item = by_label(completion(server, uri, 2, 5), "target")
    assert "textEdit" not in item
    assert item["insertText"] == "target(${1:value})$0"
    assert item["insertTextFormat"] == 2


def test_insert_replace_completion_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server, insert_replace=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())
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
    assert completion(server, uri, 3, 5) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }
