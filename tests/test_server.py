from mini_language_server.diagnostics import Diagnostic
from mini_language_server.semantic import Reference
from mini_language_server.server import LanguageServer, ServerState
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


def test_initialize_transitions_server_to_running() -> None:
    server = LanguageServer()
    response = server.handle(request("initialize", params={"capabilities": {}}))
    assert response is not None
    assert response["result"]["capabilities"] == {
        "positionEncoding": "utf-16",
        "textDocumentSync": 2,
        "definitionProvider": True,
        "referencesProvider": True,
        "renameProvider": {"prepareProvider": True},
        "hoverProvider": True,
    }
    assert server.state is ServerState.RUNNING


def test_request_before_initialize_gets_server_not_initialized() -> None:
    response = LanguageServer().handle(request("textDocument/hover"))
    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32002, "message": "Server not initialized"},
    }


def test_notification_before_initialize_is_ignored() -> None:
    server = LanguageServer()
    assert server.handle(notification("initialized")) is None
    assert server.state is ServerState.PRE_INITIALIZE


def test_unknown_request_after_initialize_is_method_not_found() -> None:
    server = initialized_server()
    response = server.handle(request("example/unknown", request_id=2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_shutdown_then_exit_is_clean() -> None:
    server = initialized_server()
    response = server.handle(request("shutdown", request_id=2))
    assert response == {"jsonrpc": "2.0", "id": 2, "result": None}
    assert server.state is ServerState.SHUTDOWN
    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 0


def test_exit_without_shutdown_is_failure() -> None:
    server = initialized_server()
    server.handle(notification("exit"))
    assert server.state is ServerState.EXITED
    assert server.exit_code == 1


def test_repeated_initialize_is_rejected() -> None:
    server = initialized_server()
    response = server.handle(request("initialize", request_id=2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32600, "message": "Initialize request already received"},
    }


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


def publish_semantics(
    server: LanguageServer,
    uri: str,
    symbols: list[Symbol],
    references: list[Reference],
) -> None:
    document = server.documents.get(uri)
    assert document is not None
    syntax = server.syntax.publish(document, tree=object())
    symbol_snapshot = server.symbols.publish(syntax, symbols)
    rebound = [
        Reference(
            reference.span,
            next(
                symbol
                for symbol in symbol_snapshot.symbols
                if symbol.name == reference.target.name
                and symbol.span == reference.target.span
            ),
        )
        for reference in references
    ]
    server.semantics.publish(symbol_snapshot, rebound)


def test_document_notifications_track_only_newer_snapshots() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "old", version=7)
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 8},
                "contentChanges": [{"text": "new"}],
            },
        )
    )
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 7},
                "contentChanges": [{"text": "stale"}],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 8
    assert document.text == "new"


def test_incremental_change_updates_document() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "hello world\n")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 11},
                        },
                        "text": "Nova",
                    }
                ],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 2
    assert document.text == "hello Nova\n"


def test_invalid_incremental_batch_does_not_advance_version() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "abc")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "text": "z",
                    },
                    {
                        "range": {
                            "start": {"line": 4, "character": 0},
                            "end": {"line": 4, "character": 0},
                        },
                        "text": "!",
                    },
                ],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 1
    assert document.text == "abc"


def test_did_close_removes_document() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "x")

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))

    assert server.documents.get(uri) is None


def test_definition_maps_utf16_position_and_span() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "let 😀foo = 1\nfoo\n"
    open_document(server, uri, text)
    symbol = Symbol("foo", "variable", Span(5, 8))
    publish_semantics(
        server,
        uri,
        [symbol],
        [Reference(Span(13, 16), symbol)],
    )

    response = server.handle(
        request(
            "textDocument/definition",
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
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 6},
                "end": {"line": 0, "character": 9},
            },
        },
    }


def test_references_are_deterministic_and_can_include_declaration() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "foo foo foo"
    open_document(server, uri, text)
    symbol = Symbol("foo", "variable", Span(0, 3))
    publish_semantics(
        server,
        uri,
        [symbol],
        [Reference(Span(8, 11), symbol), Reference(Span(4, 7), symbol)],
    )

    response = server.handle(
        request(
            "textDocument/references",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 5},
                "context": {"includeDeclaration": True},
            },
        )
    )

    assert response is not None
    assert response["result"] == [
        {
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 0, "character": 3},
            },
        },
        {
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
        },
        {
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 8},
                "end": {"line": 0, "character": 11},
            },
        },
    ]


def test_missing_semantics_returns_empty_protocol_results() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "foo")
    params = {
        "textDocument": {"uri": uri},
        "position": {"line": 0, "character": 1},
    }

    definition = server.handle(request("textDocument/definition", params=params))
    references = server.handle(
        request(
            "textDocument/references",
            request_id=2,
            params={**params, "context": {"includeDeclaration": False}},
        )
    )

    assert definition == {"jsonrpc": "2.0", "id": 1, "result": None}
    assert references == {"jsonrpc": "2.0", "id": 2, "result": []}


def test_document_change_suppresses_stale_semantic_results() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "foo foo")
    symbol = Symbol("foo", "variable", Span(0, 3))
    publish_semantics(server, uri, [symbol], [Reference(Span(4, 7), symbol)])

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "bar bar"}],
            },
        )
    )
    response = server.handle(
        request(
            "textDocument/definition",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 5},
            },
        )
    )

    assert response == {"jsonrpc": "2.0", "id": 1, "result": None}


def test_semantic_requests_reject_invalid_utf16_positions() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "😀foo")

    response = server.handle(
        request(
            "textDocument/definition",
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


def test_initialize_negotiates_first_supported_position_encoding() -> None:
    server = LanguageServer()
    response = server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "general": {
                        "positionEncodings": ["utf-8", "utf-16"],
                    }
                }
            },
        )
    )

    assert response is not None
    assert response["result"]["capabilities"]["positionEncoding"] == "utf-8"
    assert server.position_encoding == "utf-8"
    assert server.documents.position_encoding == "utf-8"


def test_initialize_falls_back_to_utf16_without_supported_advertisement() -> None:
    server = LanguageServer()
    response = server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "general": {
                        "positionEncodings": ["utf-32"],
                    }
                }
            },
        )
    )

    assert response is not None
    assert response["result"]["capabilities"]["positionEncoding"] == "utf-16"
    assert server.position_encoding == "utf-16"


def test_utf8_incremental_change_uses_negotiated_position_units() -> None:
    server = LanguageServer()
    server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "general": {"positionEncodings": ["utf-8"]},
                }
            },
        )
    )
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "a😀b\n")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 1},
                            "end": {"line": 0, "character": 5},
                        },
                        "text": "X",
                    }
                ],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.text == "aXb\n"


def test_definition_maps_utf8_position_and_span() -> None:
    server = LanguageServer()
    server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "general": {"positionEncodings": ["utf-8"]},
                }
            },
        )
    )
    uri = "file:///workspace/main.nova"
    text = "😀foo\nfoo\n"
    open_document(server, uri, text)
    symbol = Symbol("foo", "variable", Span(1, 4))
    publish_semantics(
        server,
        uri,
        [symbol],
        [Reference(Span(5, 8), symbol)],
    )

    response = server.handle(
        request(
            "textDocument/definition",
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
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
        },
    }


def test_utf8_semantic_request_rejects_position_inside_code_point() -> None:
    server = LanguageServer()
    server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "general": {"positionEncodings": ["utf-8"]},
                }
            },
        )
    )
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "😀foo")

    response = server.handle(
        request(
            "textDocument/definition",
            params={
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 2},
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_push_diagnostics_render_ranges_in_negotiated_utf8_units() -> None:
    server = LanguageServer()
    server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "general": {"positionEncodings": ["utf-8"]},
                }
            },
        )
    )
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "😀foo")
    symbol = Symbol("foo", "variable", Span(1, 4))
    publish_semantics(server, uri, [symbol], [])
    semantic = server.semantics.get(uri)
    assert semantic is not None

    assert server.publish_diagnostics(
        semantic,
        [Diagnostic(Span(1, 4), "example", code="example", source="test")],
    )
    notifications = server.drain_notifications()

    assert notifications == [
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "version": 1,
                "diagnostics": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 4},
                            "end": {"line": 0, "character": 7},
                        },
                        "severity": 1,
                        "message": "example",
                        "code": "example",
                        "source": "test",
                    }
                ],
            },
        }
    ]

def test_server_request_outbox_tracks_result_response() -> None:
    server = LanguageServer()
    request_id = server._queue_server_request("workspace/diagnostic/refresh")

    assert server._has_pending_server_request("workspace/diagnostic/refresh")
    assert server.drain_server_requests() == [
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "workspace/diagnostic/refresh",
        }
    ]
    assert server.drain_server_requests() == []

    assert server.handle(
        {"jsonrpc": "2.0", "id": request_id, "result": None}
    ) is None
    assert not server._has_pending_server_request("workspace/diagnostic/refresh")


def test_server_request_error_response_clears_pending_request() -> None:
    server = LanguageServer()
    request_id = server._queue_server_request("workspace/diagnostic/refresh")
    server.drain_server_requests()

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": "client failure"},
        }
    ) is None
    assert not server._has_pending_server_request("workspace/diagnostic/refresh")


def test_malformed_server_response_does_not_consume_pending_request() -> None:
    server = LanguageServer()
    request_id = server._queue_server_request("workspace/diagnostic/refresh")
    server.drain_server_requests()

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": None,
            "error": {"code": -32603, "message": "invalid mixed response"},
        }
    ) is None
    assert server._has_pending_server_request("workspace/diagnostic/refresh")


def test_unknown_server_response_id_is_ignored() -> None:
    server = LanguageServer()
    request_id = server._queue_server_request("workspace/diagnostic/refresh")
    server.drain_server_requests()

    assert server.handle(
        {"jsonrpc": "2.0", "id": "server:unknown", "result": None}
    ) is None
    assert server._has_pending_server_request("workspace/diagnostic/refresh")

    assert server.handle(
        {"jsonrpc": "2.0", "id": request_id, "result": None}
    ) is None


def test_missing_method_without_response_shape_remains_invalid_request() -> None:
    server = LanguageServer()

    assert server.handle({"jsonrpc": "2.0", "id": 99}) == {
        "jsonrpc": "2.0",
        "id": 99,
        "error": {"code": -32600, "message": "Invalid Request"},
    }
