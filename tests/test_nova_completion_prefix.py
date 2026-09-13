from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None


def open_nova(
    server: NovaProductLanguageServer, uri: str, text: str, *, version: int = 1
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


def complete(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    line: int,
    character: int,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
    )
    assert response is not None
    return response


def labels(response: dict[str, Any]) -> list[str]:
    return [item["label"] for item in response["result"]]


def test_completion_filters_same_file_candidates_by_typed_prefix() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn target() -> Unit {}\n"
            "fn helper() -> Unit {}\n"
            "fn main() -> Unit {\n"
            "  let tally = 1;\n"
            "  let other = 2;\n"
            "  ta\n"
            "}\n"
        ),
    )

    assert labels(completion(server, uri, 2, 5, 4)) == ["tally", "target"]


def test_completion_filters_cross_file_functions_by_typed_prefix() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library = "file:///workspace/library.nova"
    main = "file:///workspace/main.nova"
    open_nova(
        server,
        library,
        "fn target() -> Unit {}\nfn helper() -> Unit {}\n",
    )
    open_nova(server, main, "fn main() -> Unit {\n  tar\n}\n")

    assert labels(completion(server, main, 3, 1, 5)) == ["target"]


def test_empty_prefix_keeps_existing_completion_set() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn helper() -> Unit {}\nfn main(value: Int) -> Unit {\n  \n}\n",
    )

    result = labels(completion(server, uri, 4, 2, 2))
    assert "helper" in result
    assert "value" in result


def test_close_reopen_uses_new_exact_prefix_snapshot() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        "fn target() -> Unit {}\nfn helper() -> Unit {}\nfn main() -> Unit {\n  tar\n}\n",
    )
    assert labels(completion(server, uri, 5, 3, 5)) == ["target"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_nova(
        server,
        uri,
        "fn target() -> Unit {}\nfn helper() -> Unit {}\nfn main() -> Unit {\n  hel\n}\n",
        version=1,
    )

    assert labels(completion(server, uri, 6, 3, 5)) == ["helper"]


def test_same_version_replacement_rejects_prefix_filtered_completion() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn target() -> Unit {}\nfn main() -> Unit {\n  tar\n}\n")
    original = server.semantics.get(uri)
    assert original is not None
    real_prefix = server._completion_identifier_prefix

    def replace_then_prefix(text: str, offset: int) -> str:
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_prefix(text, offset)

    server._completion_identifier_prefix = replace_then_prefix  # type: ignore[method-assign]
    assert complete(server, uri, 7, 2, 5) == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32801, "message": "Content modified"},
    }
