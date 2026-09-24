from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "definition": {"linkSupport": True},
                    }
                }
            },
        )
    )
    assert response is not None
    return server


def initialized_workspace_server(root: Path) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {"diagnostic": {}},
                    "workspace": {"workspaceFolders": True},
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"}
                ],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
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


def position(text: str, marker: str, *, delta: int = 0) -> dict[str, int]:
    offset = text.index(marker) + delta
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": offset - line_start}


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    ]


def test_private_function_syntax_tracks_exact_declaration_span() -> None:
    server = NovaProductLanguageServer()
    tree = server.nova_adapter.parse(
        "private fn hidden() {}\nfn visible() {}\n"
    )

    assert [name for name, _ in tree.declarations] == ["hidden", "visible"]
    assert tree.private_declarations == (tree.declarations[0][1],)


def test_private_function_stays_local_and_is_hidden_from_direct_importer() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = (
        "private fn hidden() {}\n"
        "fn visible() {}\n"
        "fn own() { hidden() }\n"
    )
    caller = (
        "import ./provider.nova;\n"
        "fn caller() { hidden(); visible(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)

    assert "nova.unresolved-function" not in diagnostic_codes(server, provider_uri)
    assert diagnostic_codes(server, caller_uri).count("nova.unresolved-function") == 1

    hidden_definition = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "hidden", delta=1),
            },
        )
    )
    assert hidden_definition is not None
    assert hidden_definition["result"] is None

    visible_definition = server.handle(
        request(
            "textDocument/definition",
            11,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "visible", delta=1),
            },
        )
    )
    assert visible_definition is not None
    assert visible_definition["result"][0]["targetUri"] == provider_uri

    completion = server.handle(
        request(
            "textDocument/completion",
            12,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "hidden"),
            },
        )
    )
    assert completion is not None
    labels = {item["label"] for item in completion["result"]}
    assert "visible" in labels
    assert "hidden" not in labels

    references = server.handle(
        request(
            "textDocument/references",
            13,
            {
                "textDocument": {"uri": provider_uri},
                "position": position(provider, "hidden", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert {item["uri"] for item in references["result"]} == {provider_uri}

    rename = server.handle(
        request(
            "textDocument/rename",
            14,
            {
                "textDocument": {"uri": provider_uri},
                "position": position(provider, "hidden", delta=1),
                "newName": "hidden_local",
            },
        )
    )
    assert rename is not None
    assert set(rename["result"]["changes"]) == {provider_uri}


def test_private_function_is_hidden_from_legacy_global_caller() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/legacy.nova"
    open_nova(server, provider_uri, "private fn hidden() {}\n")
    caller = "fn caller() { hidden(); }\n"
    open_nova(server, caller_uri, caller)

    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)
    definition = server.handle(
        request(
            "textDocument/definition",
            20,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "hidden", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"] is None


def test_private_local_blocks_same_name_transitive_reexport() -> None:
    server = initialized_server()
    deep_uri = "file:///workspace/deep.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    deep = "fn target() {}\nfn passthrough() {}\n"
    middle = (
        "import ./deep.nova;\n"
        "private fn target() {}\n"
        "fn middle() { target() }\n"
    )
    root = (
        "import ./middle.nova;\n"
        "fn root() { target(); passthrough(); }\n"
    )
    open_nova(server, deep_uri, deep)
    open_nova(server, middle_uri, middle)
    open_nova(server, root_uri, root)

    assert "nova.unresolved-function" not in diagnostic_codes(server, middle_uri)
    root_codes = diagnostic_codes(server, root_uri)
    assert root_codes.count("nova.unresolved-function") == 1

    target_definition = server.handle(
        request(
            "textDocument/definition",
            30,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "target", delta=1),
            },
        )
    )
    assert target_definition is not None
    assert target_definition["result"] is None

    passthrough_definition = server.handle(
        request(
            "textDocument/definition",
            31,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "passthrough", delta=1),
            },
        )
    )
    assert passthrough_definition is not None
    assert passthrough_definition["result"][0]["targetUri"] == deep_uri


def test_closed_importer_does_not_see_private_function(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "private fn hidden() {}\nfn visible() {}\n",
        encoding="utf-8",
    )
    caller.write_text(
        "import ./provider.nova;\nfn caller() { hidden(); visible(); }\n",
        encoding="utf-8",
    )
    server = initialized_workspace_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            40,
            {"textDocument": {"uri": caller_uri}},
        )
    )

    assert response is not None
    assert [
        (item["code"], item["message"])
        for item in response["result"]["items"]
    ] == [
        (
            "nova.unresolved-function",
            "unresolved function 'hidden'",
        )
    ]
    assert server.documents.get(caller_uri) is None
    assert server.diagnostics.get(caller_uri) is None
