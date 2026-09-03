from mini_language_server.semantic import Reference
from mini_language_server.server import LanguageServer
from mini_language_server.source import Span
from mini_language_server.symbols import Symbol


def request(method: str, request_id: int = 1, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialized_server() -> LanguageServer:
    server = LanguageServer()
    server.handle(request("initialize"))
    return server


def open_document(server: LanguageServer, uri: str, text: str, version: int = 1) -> None:
    server.handle(
        notification(
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


def publish_semantics(server: LanguageServer, uri: str) -> None:
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=object())
    symbols = server.symbols.publish(
        syntax,
        [Symbol("foo", "variable", Span(5, 8))],
    )
    target = symbols.symbols[0]
    server.semantics.publish(
        symbols,
        [Reference(Span(13, 16), target), Reference(Span(17, 20), target)],
    )


def rename_params(uri: str, *, new_name: object = "bar") -> dict:
    return {
        "textDocument": {"uri": uri},
        "position": {"line": 1, "character": 1},
        "newName": new_name,
    }


def test_initialize_advertises_rename_provider() -> None:
    response = LanguageServer().handle(request("initialize"))
    assert response is not None
    assert response["result"]["capabilities"]["renameProvider"] is True


def test_rename_returns_deterministic_utf16_workspace_edits() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let 😀foo = 1\nfoo foo\n")
    publish_semantics(server, uri)

    response = server.handle(request("textDocument/rename", params=rename_params(uri)))

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "changes": {
                uri: [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 9},
                        },
                        "newText": "bar",
                    },
                    {
                        "range": {
                            "start": {"line": 1, "character": 0},
                            "end": {"line": 1, "character": 3},
                        },
                        "newText": "bar",
                    },
                    {
                        "range": {
                            "start": {"line": 1, "character": 4},
                            "end": {"line": 1, "character": 7},
                        },
                        "newText": "bar",
                    },
                ]
            }
        },
    }


def test_rename_missing_symbol_returns_null() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let foo = 1\n")

    response = server.handle(
        request(
            "textDocument/rename",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 0},
                "newName": "bar",
            },
        )
    )

    assert response == {"jsonrpc": "2.0", "id": 1, "result": None}


def test_rename_rejects_invalid_new_name_shape() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "foo")

    for new_name in ("", None, 42):
        response = server.handle(
            request(
                "textDocument/rename",
                params={
                    "textDocument": {"uri": uri},
                    "position": {"line": 0, "character": 0},
                    "newName": new_name,
                },
            )
        )
        assert response == {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32602, "message": "Invalid params"},
        }


def test_document_change_suppresses_stale_rename_result() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let 😀foo = 1\nfoo foo\n")
    publish_semantics(server, uri)

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "let bar = 1\nbar bar\n"}],
            },
        )
    )
    response = server.handle(
        request(
            "textDocument/rename",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 1},
                "newName": "baz",
            },
        )
    )

    assert response == {"jsonrpc": "2.0", "id": 1, "result": None}


def test_rename_rejects_invalid_utf16_position() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "😀foo")

    response = server.handle(
        request(
            "textDocument/rename",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 1},
                "newName": "bar",
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Invalid params"},
    }
