from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer

_CLIENT = (
    "import { wrapper } from ./provider.nova;\n"
    "fn main() { wrapper(); }\n"
)


def initialize(root: Path | None = None) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    params: dict[str, Any] = {"capabilities": {}}
    if root is not None:
        params["workspaceFolders"] = [{"uri": root.as_uri(), "name": "project"}]
    assert server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
    ) is not None
    server.handle({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri, "languageId": "nova", "version": 1, "text": text
                }
            },
        }
    )


def query(
    server: NovaProductLanguageServer,
    uri: str,
    method: str,
    offset: int,
    request_id: int = 10,
) -> dict[str, Any]:
    prefix = _CLIENT[:offset]
    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": f"textDocument/{method}",
            "params": {
                "textDocument": {"uri": uri},
                "position": {
                    "line": prefix.count("\n"),
                    "character": len(prefix.rsplit("\n", 1)[-1]),
                },
            },
        }
    )
    assert response is not None
    return response


def assert_signature(
    server: NovaProductLanguageServer, uri: str, result_type: str | None
) -> None:
    call = _CLIENT.rindex("wrapper")
    expected = "fn wrapper()" + (f" -> {result_type}" if result_type else "")
    assert query(server, uri, "hover", call + 1)["result"]["contents"]["value"] == expected
    assert query(server, uri, "signatureHelp", call + len("wrapper("))["result"][
        "signatures"
    ][0]["label"] == expected
    completion = query(server, uri, "completion", call + 2)["result"]
    assert next(item for item in completion if item["label"] == "wrapper")["detail"] == expected


@pytest.mark.parametrize(
    ("provider", "leaf", "result_type"),
    [
        (
            "import * as values from ./leaf.nova;\n"
            "fn wrapper() { return values :: value(); }\n",
            "fn value() { return 41; }\n",
            "Int",
        ),
        (
            "import { value as answer } from ./leaf.nova;\n"
            "fn wrapper() { return answer(); }\n",
            "fn value() { return 41; }\n",
            "Int",
        ),
        (
            "import * as values from ./leaf.nova;\n"
            "fn wrapper() { let result = values::value() return result; }\n",
            "fn value() { return 41; }\n",
            "Int",
        ),
        (
            "import * as values from ./leaf.nova;\n"
            "fn wrapper() { return values::value(); }\n",
            "fn value() -> UInt { return UInt::MAX; }\n",
            "UInt",
        ),
    ],
    ids=["namespace", "selective-alias", "local-initializer", "explicit-uint"],
)
def test_imported_result_uses_provider_scope(provider: str, leaf: str, result_type: str) -> None:
    server = initialize()
    open_nova(server, "file:///workspace/leaf.nova", leaf)
    open_nova(server, "file:///workspace/unrelated.nova", 'fn value() { return "other"; }\n')
    open_nova(server, "file:///workspace/provider.nova", provider)
    uri = "file:///workspace/client.nova"
    open_nova(server, uri, _CLIENT)

    assert_signature(server, uri, result_type)


@pytest.mark.parametrize("annotation", ["", " -> Int"], ids=["inferred", "explicit"])
def test_excluded_workspace_function_cannot_supply_result(annotation: str) -> None:
    server = initialize()
    open_nova(
        server, "file:///workspace/leaf.nova", "fn ghost()" + annotation + " { return 41; }\n"
    )
    open_nova(
        server,
        "file:///workspace/provider.nova",
        "import {} from ./leaf.nova;\nfn wrapper() { return ghost(); }\n",
    )
    uri = "file:///workspace/client.nova"
    open_nova(server, uri, _CLIENT)

    assert_signature(server, uri, None)


def test_ambiguous_visible_import_cannot_supply_result() -> None:
    server = initialize()
    open_nova(
        server, "file:///workspace/leaf.nova",
        "fn value() { return 41; }\nfn value() { return true; }\n",
    )
    open_nova(
        server, "file:///workspace/provider.nova",
        "import { value as answer } from ./leaf.nova;\nfn wrapper() { return answer(); }\n",
    )
    uri = "file:///workspace/client.nova"
    open_nova(server, uri, _CLIENT)

    assert_signature(server, uri, None)


def test_namespace_cycle_is_bounded_by_canonical_declaration() -> None:
    server = initialize()
    open_nova(
        server, "file:///workspace/leaf.nova",
        "import * as api from ./provider.nova;\nfn value() { return api::wrapper(); }\n",
    )
    open_nova(
        server, "file:///workspace/provider.nova",
        "import * as values from ./leaf.nova;\nfn wrapper() { return values::value(); }\n",
    )
    uri = "file:///workspace/client.nova"
    open_nova(server, uri, _CLIENT)

    assert_signature(server, uri, None)


def test_closed_module_results_use_indexed_provider_scope(tmp_path: Path) -> None:
    leaf = tmp_path / "leaf.nova"
    provider = tmp_path / "provider.nova"
    leaf.write_text("fn value() { return true; }\n", encoding="utf-8", newline="\n")
    provider.write_text(
        "import * as values from ./leaf.nova;\nfn wrapper() { return values::value(); }\n",
        encoding="utf-8", newline="\n",
    )
    server = initialize(tmp_path)
    uri = (tmp_path / "client.nova").as_uri()
    open_nova(server, uri, _CLIENT)

    assert server.documents.get(provider.as_uri()) is None
    assert server.documents.get(leaf.as_uri()) is None
    assert_signature(server, uri, "Bool")


def test_same_version_import_replacement_cannot_publish_stale_result(monkeypatch) -> None:
    server = initialize()
    leaf_uri = "file:///workspace/leaf.nova"
    open_nova(server, leaf_uri, "fn value() { return 41; }\n")
    open_nova(
        server, "file:///workspace/provider.nova",
        "import * as values from ./leaf.nova;\nfn wrapper() { return values::value(); }\n",
    )
    uri = "file:///workspace/client.nova"
    open_nova(server, uri, _CLIENT)
    entered = Event()
    release = Event()
    responses: list[dict[str, Any]] = []
    infer = server._bounded_function_return_type

    def pause_once(declaration, resolving=frozenset()):
        if declaration.symbol.name == "wrapper" and not entered.is_set():
            entered.set()
            assert release.wait(timeout=3)
        return infer(declaration, resolving)

    monkeypatch.setattr(server, "_bounded_function_return_type", pause_once)
    worker = Thread(
        target=lambda: responses.append(query(server, uri, "hover", _CLIENT.rindex("wrapper")))
    )
    worker.start()
    try:
        assert entered.wait(timeout=3)
        open_nova(server, leaf_uri, 'fn value() { return "replacement"; }\n')
    finally:
        release.set()
        worker.join(timeout=3)

    assert not worker.is_alive()
    assert responses == [
        {"jsonrpc": "2.0", "id": 10, "error": {"code": -32801, "message": "Content modified"}}
    ]
