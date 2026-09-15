from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer, *, snippets: bool, resolve_detail: bool = False
) -> None:
    completion_item: dict[str, Any] = {"snippetSupport": snippets}
    if resolve_detail:
        completion_item["resolveSupport"] = {"properties": ["detail"]}
    assert server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {"completion": {"completionItem": completion_item}}
                }
            },
        )
    )


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


def completion(server: NovaProductLanguageServer, uri: str, request_id: int) -> dict:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 2, "character": 2},
            },
        )
    )
    assert response is not None
    return response


def by_label(response: dict, label: str) -> dict:
    return next(item for item in response["result"] if item["label"] == label)


def test_function_completion_uses_exact_parameter_snippet_when_supported() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int, text: String) -> Bool { return true; }\n"
        "fn main() -> Unit {\n  return ();\n}\n",
    )

    assert by_label(completion(server, uri, 2), "target") == {
        "label": "target",
        "detail": "fn target(value: Int, text: String) -> Bool",
        "insertText": "target(${1:value}, ${2:text})$0",
        "insertTextFormat": 2,
        "kind": 3,
        "sortText": "2:target",
    }


def test_zero_parameter_and_cross_file_functions_get_call_snippets() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    open_nova(server, "file:///workspace/lib.nova", "fn ping() -> Unit { return (); }\n")
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit {\n  return ();\n}\n")

    assert by_label(completion(server, uri, 2), "ping")["insertText"] == "ping()$0"


def test_plain_clients_get_classified_function_completion_shape() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=False)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int) -> Int { return value; }\n"
        "fn main() -> Unit {\n  return ();\n}\n",
    )

    assert by_label(completion(server, uri, 2), "target") == {
        "label": "target",
        "detail": "fn target(value: Int) -> Int",
        "kind": 3,
        "sortText": "2:target",
    }


def test_snippets_compose_with_completion_detail_resolve() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True, resolve_detail=True)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target(value: Int) -> Int { return value; }\n"
        "fn main() -> Unit {\n  return ();\n}\n",
    )

    item = by_label(completion(server, uri, 2), "target")
    assert "detail" not in item
    assert item["insertText"] == "target(${1:value})$0"
    assert item["insertTextFormat"] == 2

    resolved = server.handle(request("completionItem/resolve", 3, item))
    assert resolved is not None
    assert resolved["result"]["detail"] == "fn target(value: Int) -> Int"


def test_snippet_completion_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    uri = "file:///workspace/main.nova"
    text = (
        "fn target(value: Int) -> Int { return value; }\n"
        "fn main() -> Unit {\n  return ();\n}\n"
    )
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
    assert completion(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }
