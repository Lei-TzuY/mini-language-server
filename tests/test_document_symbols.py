from typing import Any

from mini_language_server import NovaProductLanguageServer


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


def initialize(
    server: NovaProductLanguageServer,
    *,
    supported: bool = True,
    hierarchical: bool = False,
    position_encodings: list[str] | None = None,
) -> dict:
    capabilities: dict[str, Any] = {"textDocument": {}}
    if supported:
        capabilities["textDocument"]["documentSymbol"] = {
            "hierarchicalDocumentSymbolSupport": hierarchical,
        }
    if position_encodings is not None:
        capabilities["general"] = {"positionEncodings": position_encodings}
    response = server.handle(
        request(
            "initialize",
            params={"capabilities": capabilities},
        )
    )
    assert response is not None
    return response


def open_document(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
    language_id: str = "nova",
) -> None:
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": version,
                    "text": text,
                }
            },
        )
    )


def document_symbols(server: NovaProductLanguageServer, uri: str, request_id: int = 2):
    return server.handle(
        request(
            "textDocument/documentSymbol",
            request_id=request_id,
            params={"textDocument": {"uri": uri}},
        )
    )


def test_document_symbol_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    supported_response = initialize(supported)
    assert supported_response["result"]["capabilities"]["documentSymbolProvider"] is True

    unsupported = NovaProductLanguageServer()
    unsupported_response = initialize(unsupported, supported=False)
    assert "documentSymbolProvider" not in unsupported_response["result"]["capabilities"]


def test_flat_document_symbols_are_standard_symbol_information() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn add(left: Int, right: Int) {\n  let total = left\n}\n")

    response = document_symbols(server, uri)

    assert response is not None
    symbols = response["result"]
    assert [(item["name"], item["kind"]) for item in symbols] == [
        ("add", 12),
        ("left", 13),
        ("right", 13),
        ("total", 13),
    ]
    assert symbols[0] == {
        "name": "add",
        "kind": 12,
        "location": {
            "uri": uri,
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 6},
            },
        },
    }
    assert symbols[1]["containerName"] == "add"
    assert symbols[2]["containerName"] == "add"
    assert symbols[3] == {
        "name": "total",
        "kind": 13,
        "location": {
            "uri": uri,
            "range": {
                "start": {"line": 1, "character": 6},
                "end": {"line": 1, "character": 11},
            },
        },
        "containerName": "add",
    }
    assert all("selectionRange" not in item for item in symbols)


def test_hierarchical_document_symbols_nest_function_members() -> None:
    server = NovaProductLanguageServer()
    initialize(server, hierarchical=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn add(left: Int, right: Int) {\n  let total = left\n}\n")

    response = document_symbols(server, uri)

    assert response is not None
    assert response["result"] == [
        {
            "name": "add",
            "kind": 12,
            "range": {
                "start": {"line": 0, "character": 0},
                "end": {"line": 2, "character": 1},
            },
            "selectionRange": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 6},
            },
            "children": [
                {
                    "name": "left",
                    "kind": 13,
                    "range": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 11},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 11},
                    },
                },
                {
                    "name": "right",
                    "kind": 13,
                    "range": {
                        "start": {"line": 0, "character": 18},
                        "end": {"line": 0, "character": 23},
                    },
                    "selectionRange": {
                        "start": {"line": 0, "character": 18},
                        "end": {"line": 0, "character": 23},
                    },
                },
                {
                    "name": "total",
                    "kind": 13,
                    "range": {
                        "start": {"line": 1, "character": 6},
                        "end": {"line": 1, "character": 11},
                    },
                    "selectionRange": {
                        "start": {"line": 1, "character": 6},
                        "end": {"line": 1, "character": 11},
                    },
                },
            ],
        }
    ]


def test_hierarchical_document_symbols_keep_multiple_functions_separate() -> None:
    server = NovaProductLanguageServer()
    initialize(server, hierarchical=True)
    uri = "file:///workspace/main.nova"
    open_document(
        server,
        uri,
        (
            "fn first(value: Int) { let one = value }\n"
            "fn second(flag: Bool) { let two = flag }\n"
        ),
    )

    response = document_symbols(server, uri)

    assert response is not None
    roots = response["result"]
    assert [item["name"] for item in roots] == ["first", "second"]
    assert [child["name"] for child in roots[0]["children"]] == ["value", "one"]
    assert [child["name"] for child in roots[1]["children"]] == ["flag", "two"]


def test_hierarchical_document_symbols_use_negotiated_utf8_positions() -> None:
    server = NovaProductLanguageServer()
    response = initialize(
        server,
        hierarchical=True,
        position_encodings=["utf-8", "utf-16"],
    )
    assert response["result"]["capabilities"]["positionEncoding"] == "utf-8"
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "😀 fn add(value: Int) { let local = value }\n")

    symbols = document_symbols(server, uri)
    assert symbols is not None
    root = symbols["result"][0]

    assert root["range"]["start"] == {"line": 0, "character": 5}
    assert root["selectionRange"]["start"] == {"line": 0, "character": 8}
    assert root["children"][0]["selectionRange"]["start"] == {
        "line": 0,
        "character": 12,
    }


def test_document_symbols_return_empty_without_current_semantics() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/readme.txt"
    open_document(server, uri, "plain text", language_id="plaintext")

    assert document_symbols(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": [],
    }


def test_document_symbols_reject_same_version_semantic_replacement(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server, hierarchical=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current(value: Int) {}\n")
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_on_second_checkpoint(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_on_second_checkpoint)

    assert document_symbols(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_document_symbols_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server, hierarchical=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn current(value: Int) {}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)

    assert document_symbols(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
