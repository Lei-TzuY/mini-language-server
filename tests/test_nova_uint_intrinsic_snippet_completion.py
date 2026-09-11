from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer, *, snippets: bool) -> None:
    capabilities: dict[str, Any] = {}
    if snippets:
        capabilities = {
            "textDocument": {
                "completion": {"completionItem": {"snippetSupport": True}}
            }
        }
    assert server.handle(request("initialize", 1, {"capabilities": capabilities}))


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


def completion(
    server: NovaProductLanguageServer, uri: str, request_id: int, text: str
) -> dict[str, Any]:
    marker = text.index("/*cursor*/")
    visible = text.replace("/*cursor*/", "")
    document = server.documents.get(uri)
    assert document is not None
    assert document.text == visible
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": marker},
            },
        )
    )
    assert response is not None
    return response


def test_callable_intrinsics_use_snippets_only_when_client_supports_them() -> None:
    uri = "file:///workspace/main.nova"
    marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"

    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    open_nova(server, uri, marked.replace("/*cursor*/", ""))
    result = completion(server, uri, 2, marked)["result"]
    assert result == [
        {"label": "MIN", "detail": "constant: UInt"},
        {"label": "MAX", "detail": "constant: UInt"},
        {
            "label": "from",
            "detail": "fn UInt::from(value: Int) -> UInt",
            "insertText": "from(${1:value})",
            "insertTextFormat": 2,
        },
    ]

    plain = NovaProductLanguageServer()
    initialize(plain, snippets=False)
    open_nova(plain, uri, marked.replace("/*cursor*/", ""))
    assert completion(plain, uri, 3, marked)["result"][-1] == {
        "label": "from",
        "detail": "fn UInt::from(value: Int) -> UInt",
    }


def test_snippet_completion_rebinds_after_receiver_change() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    uri = "file:///workspace/main.nova"
    uint_marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"
    open_nova(server, uri, uint_marked.replace("/*cursor*/", ""))
    assert completion(server, uri, 2, uint_marked)["result"][-1]["insertText"] == (
        "from(${1:value})"
    )

    int_marked = "fn main() -> Unit { Int::/*cursor*/ return (); }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": int_marked.replace("/*cursor*/", "")}],
            },
        )
    )
    assert completion(server, uri, 3, int_marked)["result"] == [
        {
            "label": "from_uint",
            "detail": "fn Int::from_uint(value: UInt) -> Int",
            "insertText": "from_uint(${1:value})",
            "insertTextFormat": 2,
        }
    ]


def test_snippet_completion_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    uri = "file:///workspace/main.nova"
    marked = "fn main() -> Unit { UInt::/*cursor*/ return (); }\n"
    open_nova(server, uri, marked.replace("/*cursor*/", ""))
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
    assert completion(server, uri, 4, marked) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }
