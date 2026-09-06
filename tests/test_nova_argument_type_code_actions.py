from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeAction": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["codeActionProvider"] is True
    return server


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


def code_action(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    line: int,
    start: int,
    end: int,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": line, "character": start},
                    "end": {"line": line, "character": end},
                },
                "context": {"diagnostics": [], "only": ["quickfix"]},
            },
        )
    )
    assert result is not None
    return result


def single_action(result: dict[str, Any]) -> dict[str, Any]:
    actions = result["result"]
    assert len(actions) == 1
    return actions[0]


def test_literal_type_mismatches_get_deterministic_default_literals() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn as_string(value: String) {} "
        "fn as_bool(value: Bool) {} "
        "fn as_int(value: Int) {} "
        'fn caller() { as_string(1) as_bool("x") as_int(false) }\n'
    )
    open_nova(server, uri, text)

    cases = [
        ("as_string(1)", "1", "String", '""'),
        ('as_bool("x")', '"x"', "Bool", "false"),
        ("as_int(false)", "false", "Int", "0"),
    ]
    for request_id, (call, literal, expected_type, replacement) in enumerate(cases, start=2):
        call_start = text.index(call)
        literal_start = text.index(literal, call_start)
        action = single_action(
            code_action(
                server,
                uri,
                request_id,
                0,
                literal_start,
                literal_start + len(literal),
            )
        )
        assert expected_type in action["title"]
        edit = action["edit"]["changes"][uri][0]
        assert edit["newText"] == replacement
        assert edit["range"]["start"] == {"line": 0, "character": literal_start}
        assert edit["range"]["end"] == {
            "line": 0,
            "character": literal_start + len(literal),
        }


def test_cross_file_provider_change_recomputes_expected_literal() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: String) {}\n")
    text = "fn caller() { target(1) }\n"
    open_nova(server, caller, text)
    literal = text.index("1")

    first = single_action(code_action(server, caller, 2, 0, literal, literal + 1))
    assert first["edit"]["changes"][caller][0]["newText"] == '""'

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(value: Bool) {}\n"}],
            },
        )
    )
    current = single_action(code_action(server, caller, 3, 0, literal, literal + 1))
    assert current["edit"]["changes"][caller][0]["newText"] == "false"


def test_close_and_reopen_provider_removes_or_rebinds_type_quick_fix() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: String) {}\n")
    text = "fn caller() { target(1) }\n"
    open_nova(server, caller, text)
    literal = text.index("1")
    assert code_action(server, caller, 2, 0, literal, literal + 1)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": library}}))
    assert code_action(server, caller, 3, 0, literal, literal + 1)["result"] == []

    open_nova(server, library, "fn target(value: Int) {}\n")
    assert code_action(server, caller, 4, 0, literal, literal + 1)["result"] == []


class ReplacingProviderServer(NovaProductLanguageServer):
    def _nova_code_actions(
        self,
        uri,
        document,
        source,
        diagnostics,
        start_offset,
        end_offset,
    ):
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        library = self.documents.get("file:///workspace/library.nova")
        assert library is not None
        old = self.workspace_symbols.get(library.uri)
        assert old is not None
        replacement = self.nova_adapter.publish(self, library)
        self.workspace_symbols.replace(replacement, expected=old)
        self._publish_workspace_diagnostics()
        return actions


def test_same_version_provider_replacement_suppresses_stale_type_quick_fix() -> None:
    server = ReplacingProviderServer()
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"codeAction": {}}}},
        )
    )
    assert result is not None
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: String) {}\n")
    text = "fn caller() { target(1) }\n"
    open_nova(server, caller, text)
    literal = text.index("1")

    assert code_action(server, caller, 2, 0, literal, literal + 1) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_argument_type_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target(value: String) {} fn caller() { target(1) }\n"
    open_nova(server, uri, text)
    literal = text.index("1")
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, 2, 0, literal, literal + 1) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
