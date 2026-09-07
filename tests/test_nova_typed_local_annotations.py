from __future__ import annotations

from mini_language_server import NovaProductLanguageServer
from mini_language_server.typed_local_annotations import TypedLocalNovaFunctionAdapter


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"hover": {}, "inlayHint": {}}}},
        )
    )
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


def full_hints(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 2,
) -> dict:
    response = server.handle(
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
    assert response is not None
    return response


def local_type_labels(response: dict) -> list[str]:
    return [item["label"] for item in response["result"] if item.get("kind") == 1]


def test_adapter_treats_local_type_annotation_as_syntax_not_unresolved_name() -> None:
    tree = TypedLocalNovaFunctionAdapter.parse(
        "fn main() { let value: Int = 1 value missing }\n"
    )

    assert [(item.name, item.span.start) for item in tree.locals] == [("value", 16)]
    assert [item.name for item in tree.local_references] == ["value"]
    assert [item.name for item in tree.unresolved_names] == ["missing"]


def test_explicit_local_annotation_drives_hints_hover_and_argument_types() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        'fn takes(value: String) {} fn main() { let count: Int = "text" '
        "takes(count) count }\n"
    )
    open_nova(server, uri, text)

    assert local_type_labels(full_hints(server, uri, text)) == [": Int"]

    count_use = text.rfind("count")
    hover = server.handle(
        request(
            "textDocument/hover",
            3,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": count_use},
            },
        )
    )
    assert hover is not None
    assert hover["result"]["contents"]["value"] == "variable count: Int"

    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    argument_types = [
        diagnostic for diagnostic in snapshot.diagnostics if diagnostic.code == "nova.argument-type"
    ]
    assert len(argument_types) == 1
    assert "expected String but got Int" in argument_types[0].message
    assert all(
        not (
            diagnostic.code == "nova.unresolved-name"
            and text[diagnostic.span.start : diagnostic.span.end] == "Int"
        )
        for diagnostic in snapshot.diagnostics
    )


def test_explicit_local_annotation_recomputes_on_change_and_close_reopen() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    first = "fn main() { let value: Int = 1 value }\n"
    open_nova(server, uri, first)
    assert local_type_labels(full_hints(server, uri, first)) == [": Int"]

    changed = 'fn main() { let value: String = "x" value }\n'
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )
    assert local_type_labels(full_hints(server, uri, changed, request_id=3)) == [
        ": String"
    ]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    reopened = "fn main() { let value: Bool = true value }\n"
    open_nova(server, uri, reopened)
    assert local_type_labels(full_hints(server, uri, reopened, request_id=4)) == [
        ": Bool"
    ]


def test_explicit_local_annotation_suppresses_same_version_stale_result() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = "fn main() { let value: Int = 1 value }\n"
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
    assert full_hints(server, uri, text) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
