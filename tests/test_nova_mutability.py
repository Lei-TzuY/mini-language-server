from __future__ import annotations

from threading import Event, Thread

from mini_language_server import NovaProductLanguageServer, SourceText


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
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


def immutable_diagnostics(server: NovaProductLanguageServer, uri: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        item
        for item in snapshot.diagnostics
        if item.code == "nova.immutable-assignment"
    ]


def test_let_assignment_is_rejected_but_var_assignment_remains_mutable() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let fixed = 1; var mutable = 1; fixed = 2; mutable = 2; }\n"
    open_nova(server, uri, text)

    diagnostics = immutable_diagnostics(server, uri)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "cannot assign to immutable local 'fixed'"
    assert text[diagnostics[0].span.start : diagnostics[0].span.end] == "fixed"
    assert diagnostics[0].span.start == text.rindex("fixed")


def test_parameter_assignment_is_not_treated_as_local_mutability() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main(input: Int) { input = 2; }\n")
    assert immutable_diagnostics(server, uri) == []


def test_immutable_assignment_tracks_change_close_and_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    immutable = "fn main() { let value = 1; value = 2; }\n"
    mutable = "fn main() { var value = 1; value = 2; }\n"
    open_nova(server, uri, immutable, 1)
    assert len(immutable_diagnostics(server, uri)) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": mutable}],
            },
        )
    )
    assert immutable_diagnostics(server, uri) == []

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    assert server.diagnostics.get(uri) is None
    open_nova(server, uri, immutable, 3)
    assert len(immutable_diagnostics(server, uri)) == 1


def test_mutability_quick_fix_edits_only_exact_declaration_keyword() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 1; value = 2; }\n"
    open_nova(server, uri, text)
    diagnostic = immutable_diagnostics(server, uri)[0]

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
        if action.get("title") == "Change immutable local declaration to var"
    ]
    assert len(actions) == 1
    let_start = text.index("let")
    assert actions[0]["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": let_start},
                "end": {"line": 0, "character": let_start + 3},
            },
            "newText": "var",
        }
    ]


def test_mutability_quick_fix_rejects_superseded_same_version_diagnostic() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 1; value = 2; }\n"
    open_nova(server, uri, text, 1)

    first_snapshot = server.diagnostics.get(uri)
    document = server.documents.get(uri)
    assert first_snapshot is not None
    assert document is not None
    stale = tuple(
        item
        for item in first_snapshot.diagnostics
        if item.code == "nova.immutable-assignment"
    )
    assert len(stale) == 1

    semantic = server.nova_adapter.publish(server, document)
    current_snapshot = server.diagnostics.get(uri)
    assert current_snapshot is not None
    assert current_snapshot.semantic is semantic
    assert current_snapshot is not first_snapshot

    diagnostic = stale[0]
    actions = server._nova_code_actions(
        uri,
        document,
        SourceText(document.text),
        stale,
        diagnostic.span.start,
        diagnostic.span.end,
    )
    assert [
        action
        for action in actions
        if action.get("title") == "Change immutable local declaration to var"
    ] == []


def test_same_version_replacement_rejects_inflight_mutability_action(monkeypatch) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 1; value = 2; }\n"
    open_nova(server, uri, text)
    diagnostic = immutable_diagnostics(server, uri)[0]
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


def test_cancellation_suppresses_inflight_mutability_action(monkeypatch) -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value = 1; value = 2; }\n"
    open_nova(server, uri, text)
    diagnostic = immutable_diagnostics(server, uri)[0]
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
