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


def action_edit(result: dict[str, Any], uri: str) -> dict[str, Any]:
    actions = result["result"]
    assert len(actions) == 1
    return actions[0]["edit"]["changes"][uri][0]


def test_missing_arguments_get_executable_zero_placeholders() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target(left: Int, right: Int) {} fn caller(value: Int) { target(value) }\n"
    open_nova(server, uri, text)

    start = text.index("target(value)", text.index("caller"))
    result = code_action(server, uri, 2, 0, start, start + len("target"))
    assert result["result"][0]["title"] == "Adjust 'target' to 2 argument(s)"
    edit = action_edit(result, uri)
    assert edit["newText"] == "value, 0"


def test_extra_arguments_are_removed_at_top_level_only() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn pair(left: Int, right: Int) {} "
        "fn target(value: Int) {} "
        "fn caller(a: Int, b: Int, c: Int) { target(pair(a, b), c) }\n"
    )
    open_nova(server, uri, text)

    start = text.index("target(pair")
    edit = action_edit(code_action(server, uri, 2, 0, start, start + 6), uri)
    assert edit["newText"] == "pair(a, b)"


def test_cross_file_provider_change_replaces_current_quick_fix() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: Int) {}\n")
    text = "fn caller(value: Int) { target(value, value) }\n"
    open_nova(server, caller, text)
    start = text.index("target")
    first = action_edit(code_action(server, caller, 2, 0, start, start + 6), caller)
    assert first["newText"] == "value"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [
                    {"text": "fn target(left: Int, right: Int, third: Int) {}\n"}
                ],
            },
        )
    )
    current = action_edit(code_action(server, caller, 3, 0, start, start + 6), caller)
    assert current["newText"] == "value, value, 0"


def test_close_and_reopen_provider_rebinds_quick_fix() -> None:
    server = initialized_server()
    library = "file:///workspace/library.nova"
    caller = "file:///workspace/caller.nova"
    open_nova(server, library, "fn target(value: Int) {}\n")
    text = "fn caller(value: Int) { target(value, value) }\n"
    open_nova(server, caller, text)
    start = text.index("target")
    assert code_action(server, caller, 2, 0, start, start + 6)["result"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": library}}))
    unresolved = code_action(server, caller, 3, 0, start, start + 6)["result"]
    assert [item["title"] for item in unresolved] == ["Create function 'target'"]

    open_nova(server, library, "fn target(left: Int, right: Int) {}\n")
    assert code_action(server, caller, 4, 0, start, start + 6)["result"] == []


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


def test_same_version_provider_replacement_suppresses_stale_quick_fix() -> None:
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
    open_nova(server, library, "fn target(value: Int) {}\n")
    text = "fn caller(value: Int) { target(value, value) }\n"
    open_nova(server, caller, text)
    start = text.index("target")

    assert code_action(server, caller, 2, 0, start, start + 6) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_argument_count_quick_fix_honors_cancellation_checkpoint() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn target(left: Int, right: Int) {} fn caller(value: Int) { target(value) }\n"
    open_nova(server, uri, text)
    start = text.index("target(value)", text.index("caller"))
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert code_action(server, uri, 2, 0, start, start + 6) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
