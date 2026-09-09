from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"inlayHint": {}}}},
        )
    )
    assert result is not None
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


def inlay_hints(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 2,
) -> dict:
    result = server.handle(
        request(
            "textDocument/inlayHint",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": len(text.rstrip("\n"))},
                },
            },
        )
    )
    assert result is not None
    return result


def local_type_hints(result: dict) -> list[dict]:
    return [item for item in result["result"] if item.get("kind") == 1]


def local_type_labels(result: dict) -> list[str]:
    return [item["label"] for item in local_type_hints(result)]


def test_local_type_inlay_hints_cover_literals_and_aliases() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn caller(source: String) { let count = 1 let text = "hello" '
        "let alias = source count text alias }\n"
    )
    open_nova(server, uri, text)

    hints = local_type_hints(inlay_hints(server, uri, text))
    assert [hint["label"] for hint in hints] == [
        ": Int",
        ": String",
        ": String",
    ]
    for hint, name, annotation in zip(
        hints,
        ("count", "text", "alias"),
        (": Int", ": String", ": String"),
        strict=True,
    ):
        offset = text.index(name) + len(name)
        insertion_range = {
            "start": {"line": 0, "character": offset},
            "end": {"line": 0, "character": offset},
        }
        assert hint["position"] == insertion_range["start"]
        assert hint["textEdits"] == [
            {"range": insertion_range, "newText": annotation}
        ]


def test_local_type_inlay_hints_do_not_guess_compound_initializers() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn caller(source: Int) { let unknown = source + 1 unknown }\n"
    open_nova(server, uri, text)

    assert local_type_labels(inlay_hints(server, uri, text)) == []


def test_local_type_inlay_hints_recompute_after_change() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = 'fn caller() { let value = "text" value }\n'
    open_nova(server, uri, text)
    first = local_type_hints(inlay_hints(server, uri, text))
    assert [hint["label"] for hint in first] == [": String"]
    assert first[0]["textEdits"][0]["newText"] == ": String"

    changed = "fn caller() { let value = 1 value }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )
    second = local_type_hints(inlay_hints(server, uri, changed, request_id=3))
    assert [hint["label"] for hint in second] == [": Int"]
    assert second[0]["textEdits"][0]["newText"] == ": Int"


def test_local_type_inlay_hints_rebuild_edits_after_close_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    first = "fn caller() { let value = 1 value }\n"
    open_nova(server, uri, first)
    assert local_type_hints(inlay_hints(server, uri, first))[0]["textEdits"][0][
        "newText"
    ] == ": Int"

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    reopened = 'fn caller() { let value = "text" value }\n'
    open_nova(server, uri, reopened)
    hints = local_type_hints(inlay_hints(server, uri, reopened, request_id=3))
    assert [hint["label"] for hint in hints] == [": String"]
    assert hints[0]["textEdits"][0]["newText"] == ": String"


def test_local_type_inlay_hints_suppress_same_version_replacement() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn caller() { let value = 1 value }\n"
    open_nova(server, uri, text)
    original = server.workspace_symbols.get(uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert inlay_hints(server, uri, text) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_local_type_inlay_hints_honor_cancellation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn caller() { let value = 1 value }\n"
    open_nova(server, uri, text)
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert inlay_hints(server, uri, text) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
