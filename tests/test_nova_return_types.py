from __future__ import annotations

from mini_language_server import NovaProductLanguageServer
from mini_language_server.return_types import ReturnTypeNovaFunctionAdapter


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


def codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item.code or "" for item in snapshot.diagnostics]


def test_return_and_boolean_literals_are_not_unresolved_names() -> None:
    tree = ReturnTypeNovaFunctionAdapter.parse(
        "fn truth() -> Bool { return true } fn lie() -> Bool { return false }\n"
    )
    assert tree.unresolved_names == ()


def test_literal_return_mismatch_is_deterministic_and_exact_spanned() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn count() -> Int { return "wrong"; }\n'
    open_nova(server, uri, text)

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    diagnostics = [item for item in snapshot.diagnostics if item.code == "nova.return-type"]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.message == "return type mismatch: expected 'Int', got 'String'"
    assert text[diagnostic.span.start : diagnostic.span.end] == '"wrong"'


def test_matching_literal_return_types_remain_clean() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        'fn i() -> Int { return 1; } fn s() -> String { return "ok"; } '
        "fn b() -> Bool { return true; }\n",
    )
    assert "nova.return-type" not in codes(server, uri)


def test_did_change_replaces_return_diagnostic_on_new_snapshot() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    bad = 'fn value() -> Int { return "bad"; }\n'
    good = "fn value() -> Int { return 7; }\n"
    open_nova(server, uri, bad)
    assert "nova.return-type" in codes(server, uri)

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": good}],
            },
        )
    )
    assert "nova.return-type" not in codes(server, uri)


def test_close_reopen_rebuilds_return_diagnostics() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn value() -> Int { return "bad"; }\n')
    assert "nova.return-type" in codes(server, uri)
    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(server, uri, "fn value() -> Int { return 9; }\n", version=1)
    assert "nova.return-type" not in codes(server, uri)
