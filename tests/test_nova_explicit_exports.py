from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


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
                "workspaceFolders": [{"uri": root.as_uri(), "name": "workspace"}],
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


def test_export_parser_tracks_exact_names_and_explicit_empty_list() -> None:
    server = NovaProductLanguageServer()
    text = "export { alpha, beta };\nfn alpha() {}\nfn beta() {}\n"
    tree = server.nova_adapter.parse(text)

    assert tree.has_export_list is True
    assert [item.name for item in tree.exports] == ["alpha", "beta"]
    assert [text[item.span.start : item.span.end] for item in tree.exports] == [
        "alpha",
        "beta",
    ]

    empty = server.nova_adapter.parse("export {};\nfn alpha() {}\n")
    assert empty.has_export_list is True
    assert empty.exports == ()


def test_explicit_export_list_filters_direct_import_tooling() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = (
        "fn visible() {}\n"
        "fn hidden() {}\n"
        "export { visible };\n"
    )
    caller = (
        "import ./provider.nova;\n"
        "fn caller() { visible(); hidden(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)

    assert diagnostic_codes(server, caller_uri).count("nova.unresolved-function") == 1

    visible = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "visible", delta=1),
            },
        )
    )
    assert visible is not None
    assert visible["result"][0]["targetUri"] == provider_uri

    hidden = server.handle(
        request(
            "textDocument/definition",
            11,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "hidden", delta=1),
            },
        )
    )
    assert hidden is not None
    assert hidden["result"] is None

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


def test_explicit_empty_export_list_exports_nothing() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/empty-provider.nova"
    caller_uri = "file:///workspace/empty-caller.nova"
    open_nova(server, provider_uri, "fn hidden() {}\nexport {};\n")
    caller = "import ./empty-provider.nova;\nfn caller() { hidden(); }\n"
    open_nova(server, caller_uri, caller)

    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)


def test_explicit_export_list_supports_bounded_reexport() -> None:
    server = initialized_server()
    deep_uri = "file:///workspace/deep.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, deep_uri, "fn transit() {}\n")
    open_nova(
        server,
        middle_uri,
        (
            "import ./deep.nova;\n"
            "fn middle_only() {}\n"
            "export { transit };\n"
        ),
    )
    root = (
        "import ./middle.nova;\n"
        "fn root() { transit(); middle_only(); }\n"
    )
    open_nova(server, root_uri, root)

    assert diagnostic_codes(server, root_uri).count("nova.unresolved-function") == 1
    definition = server.handle(
        request(
            "textDocument/definition",
            20,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "transit", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == deep_uri


def test_export_diagnostics_reject_private_missing_ambiguous_and_duplicate() -> None:
    server = initialized_server()
    left_uri = "file:///workspace/left.nova"
    right_uri = "file:///workspace/right.nova"
    module_uri = "file:///workspace/module.nova"
    open_nova(server, left_uri, "fn shared() {}\n")
    open_nova(server, right_uri, "fn shared() {}\n")
    module = (
        "import ./left.nova;\n"
        "import ./right.nova;\n"
        "private fn hidden() {}\n"
        "fn own() {}\n"
        "export { hidden, missing, shared, own, own };\n"
    )
    open_nova(server, module_uri, module)

    codes = diagnostic_codes(server, module_uri)
    assert "nova.private-export" in codes
    assert "nova.unresolved-export" in codes
    assert "nova.ambiguous-export" in codes
    assert "nova.duplicate-export" in codes


def test_export_list_does_not_change_legacy_module_without_list() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/legacy-provider.nova"
    caller_uri = "file:///workspace/legacy-caller.nova"
    open_nova(server, provider_uri, "fn first() {}\nfn second() {}\n")
    caller = (
        "import ./legacy-provider.nova;\n"
        "fn caller() { first(); second(); }\n"
    )
    open_nova(server, caller_uri, caller)

    assert "nova.unresolved-function" not in diagnostic_codes(server, caller_uri)


def test_closed_importer_obeys_explicit_export_list(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn visible() {}\nfn hidden() {}\nexport { visible };\n",
        encoding="utf-8",
    )
    caller.write_text(
        "import ./provider.nova;\nfn caller() { visible(); hidden(); }\n",
        encoding="utf-8",
    )
    server = initialized_workspace_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            30,
            {"textDocument": {"uri": caller_uri}},
        )
    )

    assert response is not None
    assert [
        item["code"]
        for item in response["result"]["items"]
        if item["code"] == "nova.unresolved-function"
    ] == ["nova.unresolved-function"]
    assert server.documents.get(caller_uri) is None
    assert server.diagnostics.get(caller_uri) is None
