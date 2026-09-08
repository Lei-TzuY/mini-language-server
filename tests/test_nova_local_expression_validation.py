from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server(*, code_actions: bool = False) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    capabilities: dict[str, Any] = {}
    if code_actions:
        capabilities = {"textDocument": {"codeAction": {}}}
    response = server.handle(request("initialize", 1, {"capabilities": capabilities}))
    assert response is not None
    return server


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
) -> None:
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


def local_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item for item in snapshot.diagnostics if item.code == "nova.local-type"]


def test_arithmetic_initializer_participates_in_explicit_local_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let ready: Bool = 1 + 2 * 3\n}\n"
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local type mismatch: expected 'Bool', got 'Int'"
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "1 + 2 * 3"


def test_comparison_initializer_participates_in_explicit_local_validation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let count: Int = 1 < 2\n}\n"
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local type mismatch: expected 'Int', got 'Bool'"
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "1 < 2"


def test_unknown_compound_initializer_remains_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, 'fn main() { let count: Int = "a" + "b"\n}\n')
    assert local_diagnostics(server, uri) == []


def test_same_line_local_boundaries_remain_deterministic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn main() { let count: Int = "bad" '
        "let ready: Bool = 1 + 2 }\n"
    )
    open_nova(server, uri, text)

    diagnostics = local_diagnostics(server, uri)
    assert [item.message for item in diagnostics] == [
        "local type mismatch: expected 'Int', got 'String'",
        "local type mismatch: expected 'Bool', got 'Int'",
    ]
    assert [text[item.span.start : item.span.end] for item in diagnostics] == [
        '"bad"',
        "1 + 2",
    ]


def test_cross_file_change_rebinds_arithmetic_initializer_type() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, helper_uri, "fn helper() -> Int { return 1; }\n")
    open_nova(server, main_uri, "fn main() { let ready: Bool = helper() + 1\n}\n")
    diagnostics = local_diagnostics(server, main_uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "local type mismatch: expected 'Bool', got 'Int'"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": 'fn helper() -> String { return "value"; }\n'}
                ],
            },
        )
    )
    assert local_diagnostics(server, main_uri) == []


def test_expression_local_type_quick_fix_replaces_exact_initializer_span() -> None:
    server = initialized_server(code_actions=True)
    uri = "file:///workspace/main.nova"
    text = "fn main() { let count: Int = 1 < 2 }\n"
    open_nova(server, uri, text)
    start = text.index("1 < 2")
    response = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": start},
                    "end": {"line": 0, "character": start + len("1 < 2")},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert response is not None
    actions = response["result"]
    assert len(actions) == 1
    edit = actions[0]["edit"]["changes"][uri][0]
    assert edit["newText"] == "0"
    assert edit["range"]["start"] == {"line": 0, "character": start}
    assert edit["range"]["end"] == {
        "line": 0,
        "character": start + len("1 < 2"),
    }
