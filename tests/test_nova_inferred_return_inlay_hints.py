from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"inlayHint": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["inlayHintProvider"] is True


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


def hints(
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


def return_hints(result: dict) -> list[dict]:
    return [item for item in result["result"] if str(item["label"]).startswith(" ->")]


def test_inferred_return_hint_surfaces_bounded_type_only_for_unannotated_function() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn inferred() { return 1 } fn explicit() -> Int { return 1 }\n"
    open_nova(server, uri, text)
    insertion_character = text.index(")") + 1

    assert return_hints(hints(server, uri, text)) == [
        {
            "position": {
                "line": 0,
                "character": insertion_character,
            },
            "label": " -> Int",
            "kind": 1,
            "textEdits": [
                {
                    "range": {
                        "start": {"line": 0, "character": insertion_character},
                        "end": {"line": 0, "character": insertion_character},
                    },
                    "newText": " -> Int",
                }
            ],
            "paddingLeft": True,
        }
    ]


def test_inferred_return_hint_stays_conservative_for_conflicts_and_ambiguity() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    conflict_uri = "file:///workspace/conflict.nova"
    conflict = 'fn mixed(flag: Bool) { if (flag) { return 1 } else { return "x" } }\n'
    open_nova(server, conflict_uri, conflict)
    assert return_hints(hints(server, conflict_uri, conflict)) == []

    duplicate_a = "file:///workspace/a.nova"
    duplicate_b = "file:///workspace/b.nova"
    text = "fn duplicate() { return 1 }\n"
    open_nova(server, duplicate_a, text)
    open_nova(server, duplicate_b, text)
    assert return_hints(hints(server, duplicate_a, text, request_id=3)) == []


def test_inferred_return_hint_tracks_cross_file_change_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    helper_uri = "file:///workspace/helper.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    wrapper = "fn wrapper() { return helper() }\n"
    open_nova(server, helper_uri, "fn helper() -> Int { return 1 }\n")
    open_nova(server, wrapper_uri, wrapper)
    assert return_hints(hints(server, wrapper_uri, wrapper))[0]["label"] == " -> Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": 'fn helper() -> String { return "value" }\n'}
                ],
            },
        )
    )
    assert return_hints(hints(server, wrapper_uri, wrapper, request_id=3))[0]["label"] == (
        " -> String"
    )

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": helper_uri}}))
    assert return_hints(hints(server, wrapper_uri, wrapper, request_id=4)) == []

    open_nova(server, helper_uri, "fn helper() -> Bool { return true }\n")
    assert return_hints(hints(server, wrapper_uri, wrapper, request_id=5))[0]["label"] == (
        " -> Bool"
    )


def test_inferred_return_hint_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    helper_uri = "file:///workspace/helper.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    wrapper = "fn wrapper() { return helper() }\n"
    open_nova(server, helper_uri, "fn helper() -> Int { return 1 }\n")
    open_nova(server, wrapper_uri, wrapper)
    original = server.workspace_symbols.get(helper_uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(helper_uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert hints(server, wrapper_uri, wrapper) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_inferred_return_hint_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = "fn inferred() { return 1 }\n"
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
    assert hints(server, uri, text) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
