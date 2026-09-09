from __future__ import annotations

from threading import Event, Thread

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, version: int = 1
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


def invalid_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return tuple(
        diagnostic
        for diagnostic in snapshot.diagnostics
        if diagnostic.code == "nova.invalid-loop-control"
    )


def test_break_and_continue_are_valid_inside_while_and_not_unresolved() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main(flag: Bool) { while (flag) { break; continue; } }\n"
    open_nova(server, uri, text)

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    assert invalid_diagnostics(server, uri) == ()
    assert all(
        diagnostic.code != "nova.unresolved-name"
        or "break" not in diagnostic.message
        and "continue" not in diagnostic.message
        for diagnostic in snapshot.diagnostics
    )


def test_nested_block_inside_while_is_valid_but_sibling_block_is_not() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) { "
        "while (flag) { if (flag) { break; } } "
        "if (flag) { continue; } }\n"
    )
    open_nova(server, uri, text)

    diagnostics = invalid_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "continue;"


def test_invalid_loop_control_has_exact_quick_fix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { break; }\n"
    open_nova(server, uri, text)
    diagnostic = invalid_diagnostics(server, uri)[0]

    response = server.handle(
        request(
            "textDocument/codeAction",
            2,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": diagnostic.span.start},
                    "end": {"line": 0, "character": diagnostic.span.end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    actions = [
        action
        for action in response["result"]
        if action.get("title") == "Remove invalid loop-control statement"
    ]
    assert len(actions) == 1
    assert actions[0]["diagnostics"][0]["code"] == "nova.invalid-loop-control"
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": diagnostic.span.start},
                "end": {"line": 0, "character": diagnostic.span.end},
            },
            "newText": "",
        }
    ]


def test_loop_control_rebinds_across_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    invalid = "fn main(flag: Bool) { break; }\n"
    valid = "fn main(flag: Bool) { while (flag) { break; } }\n"
    open_nova(server, uri, invalid, 1)
    assert len(invalid_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": valid}],
            },
        )
    )
    assert invalid_diagnostics(server, uri) == ()

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, invalid, 3)
    assert len(invalid_diagnostics(server, uri)) == 1


def test_same_version_reanalysis_rejects_stale_loop_control_action(monkeypatch) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { break; }\n"
    open_nova(server, uri, text)
    diagnostic = invalid_diagnostics(server, uri)[0]
    original = type(server)._nova_code_actions

    def replacing_actions(self, *args, **kwargs):
        actions = original(self, *args, **kwargs)
        document = self.documents.get(uri)
        assert document is not None
        self.nova_adapter.publish(self, document)
        return actions

    monkeypatch.setattr(type(server), "_nova_code_actions", replacing_actions)
    response = server.handle(
        request(
            "textDocument/codeAction",
            3,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": diagnostic.span.start},
                    "end": {"line": 0, "character": diagnostic.span.end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_cancellation_suppresses_inflight_loop_control_action(monkeypatch) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { break; }\n"
    open_nova(server, uri, text)
    diagnostic = invalid_diagnostics(server, uri)[0]
    original = type(server)._nova_code_actions
    entered = Event()
    release = Event()
    responses: list[dict | None] = []

    def blocked_actions(self, *args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(server), "_nova_code_actions", blocked_actions)
    message = request(
        "textDocument/codeAction",
        4,
        {
            "textDocument": {"uri": uri},
            "range": {
                "start": {"line": 0, "character": diagnostic.span.start},
                "end": {"line": 0, "character": diagnostic.span.end},
            },
            "context": {"diagnostics": []},
        },
    )
    thread = Thread(target=lambda: responses.append(server.handle(message)))
    thread.start()
    assert entered.wait(timeout=5)
    assert server.handle(notify("$/cancelRequest", {"id": 4})) is None
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 4,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
