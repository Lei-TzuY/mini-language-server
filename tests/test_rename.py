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


def initialized_server(*, document_changes: bool = False) -> LanguageServer:
    server = LanguageServer()
    capabilities = (
        {"workspace": {"workspaceEdit": {"documentChanges": True}}}
        if document_changes
        else {}
    )
    server.handle(request("initialize", params={"capabilities": capabilities}))
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


def test_initialize_advertises_prepare_rename_provider() -> None:
    response = LanguageServer().handle(request("initialize"))
    assert response is not None
    assert response["result"]["capabilities"]["renameProvider"] == {
        "prepareProvider": True
    }


def test_prepare_rename_returns_utf16_range_and_placeholder() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let 😀foo = 1\nfoo foo\n")
    publish_semantics(server, uri)

    response = server.handle(
        request(
            "textDocument/prepareRename",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 1, "character": 1},
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "range": {
                "start": {"line": 0, "character": 6},
                "end": {"line": 0, "character": 9},
            },
            "placeholder": "foo",
        },
    }


def test_prepare_rename_missing_symbol_returns_null() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let foo = 1\n")

    response = server.handle(
        request(
            "textDocument/prepareRename",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 0},
            },
        )
    )

    assert response == {"jsonrpc": "2.0", "id": 1, "result": None}


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


def test_prepare_rename_rejects_invalid_utf16_position() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "😀foo")

    response = server.handle(
        request(
            "textDocument/prepareRename",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 1},
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Invalid params"},
    }


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

def test_rename_returns_versioned_document_changes_when_negotiated() -> None:
    server = initialized_server(document_changes=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let 😀foo = 1\nfoo foo\n", version=7)
    publish_semantics(server, uri)

    response = server.handle(request("textDocument/rename", params=rename_params(uri)))

    assert response is not None
    result = response["result"]
    assert "changes" not in result
    assert result["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": 7},
            "edits": [
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
            ],
        }
    ]


def test_document_changes_capability_must_be_literal_true() -> None:
    server = LanguageServer()
    server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "workspace": {"workspaceEdit": {"documentChanges": False}}
                }
            },
        )
    )
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "let 😀foo = 1\nfoo foo\n")
    publish_semantics(server, uri)

    response = server.handle(request("textDocument/rename", params=rename_params(uri)))

    assert response is not None
    assert "changes" in response["result"]
    assert "documentChanges" not in response["result"]
