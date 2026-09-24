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
                    "textDocument": {"definition": {"linkSupport": True}},
                }
            },
        )
    )
    assert response is not None
    return server


def initialized_workspace_server(
    root: Path,
    *,
    will_rename: bool = False,
    document_changes: bool = False,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    workspace: dict[str, Any] = {"workspaceFolders": True}
    if will_rename:
        workspace["fileOperations"] = {"willRename": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {"diagnostic": {}},
                    "workspace": workspace,
                },
                "workspaceFolders": [{"uri": root.as_uri(), "name": "workspace"}],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
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


def position(text: str, marker: str, *, delta: int = 0) -> dict[str, int]:
    offset = text.index(marker) + delta
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": offset - line_start}


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [item.code for item in snapshot.diagnostics if item.code is not None]


def test_selective_import_parser_tracks_exact_names_and_empty_list() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import { alpha, beta } from ./provider.nova;\n"
        "import {} from ./empty.nova;\n"
        "fn main() {}\n"
    )

    tree = server.nova_adapter.parse(text)

    assert len(tree.imports) == 2
    first, second = tree.imports
    assert first.has_name_list is True
    assert first.path == "./provider.nova"
    assert text[first.span.start : first.span.end] == "./provider.nova"
    assert [item.name for item in first.names] == ["alpha", "beta"]
    assert [text[item.span.start : item.span.end] for item in first.names] == [
        "alpha",
        "beta",
    ]
    assert second.has_name_list is True
    assert second.names == ()
    assert second.path == "./empty.nova"


def test_selective_import_filters_direct_tooling() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = "fn visible() {}\nfn hidden() {}\n"
    caller = (
        "import { visible } from ./provider.nova;\n"
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


def test_explicit_empty_selective_import_exposes_nothing() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn hidden() {}\n")
    caller = "import {} from ./provider.nova;\nfn caller() { hidden(); }\n"
    open_nova(server, caller_uri, caller)

    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)


def test_selective_import_composes_with_explicit_reexport() -> None:
    server = initialized_server()
    deep_uri = "file:///workspace/deep.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, deep_uri, "fn transit() {}\nfn omitted() {}\n")
    open_nova(
        server,
        middle_uri,
        "import { transit } from ./deep.nova;\nexport { transit };\n",
    )
    root = "import ./middle.nova;\nfn root() { transit(); omitted(); }\n"
    open_nova(server, root_uri, root)

    assert diagnostic_codes(server, root_uri).count("nova.unresolved-function") == 1
    assert "nova.unresolved-export" not in diagnostic_codes(server, middle_uri)
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


def test_selective_import_reports_duplicate_unresolved_and_ambiguous_names() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    left_uri = "file:///workspace/left.nova"
    right_uri = "file:///workspace/right.nova"
    middle_uri = "file:///workspace/middle.nova"
    caller_uri = "file:///workspace/caller.nova"

    open_nova(
        server,
        provider_uri,
        "fn visible() {}\nfn omitted() {}\nexport { visible };\n",
    )
    open_nova(server, left_uri, "fn shared() {}\n")
    open_nova(server, right_uri, "fn shared() {}\n")
    open_nova(
        server,
        middle_uri,
        "import ./left.nova;\nimport ./right.nova;\n",
    )
    caller = (
        "import { visible, missing, visible } from ./provider.nova;\n"
        "import { shared } from ./middle.nova;\n"
        "fn caller() {}\n"
    )
    open_nova(server, caller_uri, caller)

    codes = diagnostic_codes(server, caller_uri)
    assert "nova.duplicate-import-name" in codes
    assert "nova.unresolved-import-name" in codes
    assert "nova.ambiguous-import-name" in codes


def test_closed_importer_obeys_selective_import_view(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn visible() {}\nfn hidden() {}\n", encoding="utf-8")
    caller.write_text(
        (
            "import { visible } from ./provider.nova;\n"
            "fn caller() { visible(); hidden(); }\n"
        ),
        encoding="utf-8",
    )
    server = initialized_workspace_server(tmp_path)
    caller_uri = caller.as_uri()

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


def test_will_rename_rewrites_only_selective_import_path(tmp_path: Path) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n", encoding="utf-8")
    renamed = tmp_path / "renamed.nova"
    server = initialized_workspace_server(
        tmp_path,
        will_rename=True,
        document_changes=True,
    )
    importer_uri = (tmp_path / "main.nova").as_uri()
    source = "import { dep } from ./dep.nova;\nfn main() { dep(); }\n"
    open_nova(server, importer_uri, source, version=7)

    response = server.handle(
        request(
            "workspace/willRenameFiles",
            40,
            {"files": [{"oldUri": target.as_uri(), "newUri": renamed.as_uri()}]},
        )
    )

    assert response is not None
    path_start = source.index("./dep.nova")
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": importer_uri, "version": 7},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": path_start},
                        "end": {
                            "line": 0,
                            "character": path_start + len("./dep.nova"),
                        },
                    },
                    "newText": "./renamed.nova",
                }
            ],
        }
    ]
