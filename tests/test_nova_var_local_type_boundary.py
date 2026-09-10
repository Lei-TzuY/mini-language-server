from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str, version: int = 1) -> None:
    server.handle(
        notify(
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


def local_type_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.local-type"]


def test_same_line_var_starts_a_new_typed_local_initializer() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn main() { let count: Int = "bad" var ready: Bool = 1 }\n'
    open_nova(server, uri, text)

    diagnostics = local_type_diagnostics(server, uri)
    assert [item.message for item in diagnostics] == [
        "local type mismatch: expected 'Int', got 'String'",
        "local type mismatch: expected 'Bool', got 'Int'",
    ]
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        '"bad"',
        "1",
    ]


def test_var_prefix_inside_identifier_does_not_split_initializer() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(variety: Int) { let count: Int = variety }\n"
    open_nova(server, uri, text)

    assert local_type_diagnostics(server, uri) == []


def test_did_change_rebinds_same_line_var_boundary_to_new_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn main() { let count: Int = "bad" var ready: Bool = 1 }\n')
    assert len(local_type_diagnostics(server, uri)) == 2

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn main() { let count: Int = 1 var ready: Bool = true }\n"}
                ],
            },
        )
    )

    assert local_type_diagnostics(server, uri) == []
