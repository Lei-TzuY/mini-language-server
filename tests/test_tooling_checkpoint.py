from mini_language_server import Diagnostic, LanguageServer, Reference, Span, Symbol


def request(method: str, request_id: int, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def position_params(uri: str, character: int) -> dict:
    return {
        "textDocument": {"uri": uri},
        "position": {"line": 0, "character": character},
    }


def test_document_to_semantic_navigation_checkpoint() -> None:
    server = LanguageServer()
    initialize = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert initialize is not None

    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "let foo = foo\n",
                }
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=("module",))
    symbols = server.symbols.publish(
        syntax,
        [Symbol("foo", "variable", Span(4, 7))],
    )
    target = symbols.symbols[0]
    semantic = server.semantics.publish(
        symbols,
        [Reference(Span(10, 13), target)],
    )

    assert server.publish_diagnostics(
        semantic,
        [Diagnostic(Span(10, 13), "example diagnostic", source="nova")],
    )
    diagnostic_notifications = server.drain_notifications()
    assert len(diagnostic_notifications) == 1
    assert diagnostic_notifications[0]["method"] == "textDocument/publishDiagnostics"
    assert diagnostic_notifications[0]["params"]["version"] == 1

    definition = server.handle(
        request("textDocument/definition", 2, position_params(uri, 11))
    )
    assert definition == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
        },
    }

    references_params = position_params(uri, 11)
    references_params["context"] = {"includeDeclaration": True}
    references = server.handle(request("textDocument/references", 3, references_params))
    assert references is not None
    assert [item["range"] for item in references["result"]] == [
        {
            "start": {"line": 0, "character": 4},
            "end": {"line": 0, "character": 7},
        },
        {
            "start": {"line": 0, "character": 10},
            "end": {"line": 0, "character": 13},
        },
    ]

    rename_params = position_params(uri, 11)
    rename_params["newName"] = "bar"
    rename = server.handle(request("textDocument/rename", 4, rename_params))
    assert rename is not None
    assert [edit["range"] for edit in rename["result"]["changes"][uri]] == [
        {
            "start": {"line": 0, "character": 4},
            "end": {"line": 0, "character": 7},
        },
        {
            "start": {"line": 0, "character": 10},
            "end": {"line": 0, "character": 13},
        },
    ]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "let foo = bar\n"}],
            },
        )
    )

    assert server.semantics.get(uri) is None
    stale_definition = server.handle(
        request("textDocument/definition", 5, position_params(uri, 11))
    )
    assert stale_definition == {"jsonrpc": "2.0", "id": 5, "result": None}
    cleared = server.drain_notifications()
    assert cleared == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {"uri": uri, "diagnostics": [], "version": 2},
        }
    ]
