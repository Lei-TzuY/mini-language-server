from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.nova import NovaFunctionSyntax


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    folder: Path | None = None,
    document_links: bool = False,
    will_rename: bool = False,
    document_changes: bool = False,
) -> None:
    text_document: dict[str, Any] = {"definition": {"linkSupport": True}}
    if document_links:
        text_document["documentLink"] = {}
    workspace: dict[str, Any] = {}
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": text_document,
            "workspace": workspace,
        }
    }
    if folder is not None:
        workspace["workspaceFolders"] = True
        params["workspaceFolders"] = [
            {"uri": folder.as_uri(), "name": "workspace"}
        ]
    if will_rename:
        workspace["fileOperations"] = {"willRename": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}

    response = server.handle(request("initialize", 1, params))
    assert response is not None
    if folder is not None:
        server.handle(notify("initialized", {}))


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
        item.code
        for item in snapshot.diagnostics
        if item.code is not None
    ]


def test_parser_tracks_top_level_wildcard_reexports() -> None:
    server = NovaProductLanguageServer()
    text = (
        "export * from ./dep.nova;\n"
        "export * from @/api/provider.nova;\n"
        "fn nested() {\n"
        "  export * from ./ignored.nova;\n"
        "}\n"
    )

    tree = server.nova_adapter.parse(text)

    assert isinstance(tree, NovaFunctionSyntax)
    assert tree.has_export_list is True
    assert tree.exports == ()
    assert [item.path for item in tree.wildcard_exports] == [
        "./dep.nova",
        "@/api/provider.nova",
    ]
    assert [
        text[item.span.start : item.span.end]
        for item in tree.wildcard_exports
    ] == ["./dep.nova", "@/api/provider.nova"]


def test_wildcard_reexport_is_outward_only_and_preserves_export_rules() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    facade_uri = "file:///workspace/facade.nova"
    root_uri = "file:///workspace/root.nova"
    provider = (
        "fn visible() {}\n"
        "fn omitted() {}\n"
        "private fn hidden() {}\n"
        "export { visible };\n"
    )
    facade = (
        "export * from ./provider.nova;\n"
        "fn local() { visible(); }\n"
    )
    root = (
        "import ./facade.nova;\n"
        "fn root() { visible(); omitted(); hidden(); local(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, facade_uri, facade)
    open_nova(server, root_uri, root)

    assert diagnostic_codes(server, facade_uri).count("nova.unresolved-function") == 1
    assert diagnostic_codes(server, root_uri).count("nova.unresolved-function") == 3

    definition = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "visible", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == provider_uri

    completion = server.handle(
        request(
            "textDocument/completion",
            11,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "visible"),
            },
        )
    )
    assert completion is not None
    labels = {item["label"] for item in completion["result"]}
    assert "visible" in labels
    assert "omitted" not in labels
    assert "hidden" not in labels
    assert "local" not in labels


def test_local_declaration_blocks_same_named_wildcard_reexport() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider-shadow.nova"
    facade_uri = "file:///workspace/facade-shadow.nova"
    root_uri = "file:///workspace/root-shadow.nova"
    open_nova(
        server,
        provider_uri,
        "fn shared() {}\nexport { shared };\n",
    )
    open_nova(
        server,
        facade_uri,
        (
            "export * from ./provider-shadow.nova;\n"
            "fn shared() {}\n"
        ),
    )
    root = "import ./facade-shadow.nova;\nfn root() { shared(); }\n"
    open_nova(server, root_uri, root)

    assert "nova.unresolved-function" in diagnostic_codes(server, root_uri)


def test_wildcard_reexport_chain_preserves_canonical_rename() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider-chain.nova"
    middle_uri = "file:///workspace/middle-chain.nova"
    facade_uri = "file:///workspace/facade-chain.nova"
    root_uri = "file:///workspace/root-chain.nova"
    provider = "fn target() {}\nexport { target };\n"
    open_nova(server, provider_uri, provider)
    open_nova(
        server,
        middle_uri,
        "export * from ./provider-chain.nova;\n",
    )
    open_nova(
        server,
        facade_uri,
        "export * from ./middle-chain.nova;\n",
    )
    root = "import ./facade-chain.nova;\nfn root() { target(); }\n"
    open_nova(server, root_uri, root)

    definition = server.handle(
        request(
            "textDocument/definition",
            20,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "target", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == provider_uri

    rename = server.handle(
        request(
            "textDocument/rename",
            21,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "target", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert rename is not None
    assert set(rename["result"]["changes"]) == {provider_uri, root_uri}
    assert all(
        edit["newText"] == "renamed"
        for edits in rename["result"]["changes"].values()
        for edit in edits
    )


def test_outward_alias_rename_crosses_wildcard_reexport_transparently() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider-alias.nova"
    api_uri = "file:///workspace/api-alias.nova"
    facade_uri = "file:///workspace/facade-alias.nova"
    consumer_uri = "file:///workspace/consumer-alias.nova"
    open_nova(server, provider_uri, "fn target() {}\nexport { target };\n")
    api = (
        "import { target as public } from ./provider-alias.nova;\n"
        "export { public };\n"
    )
    open_nova(server, api_uri, api)
    open_nova(
        server,
        facade_uri,
        "export * from ./api-alias.nova;\n",
    )
    consumer = (
        "import ./facade-alias.nova;\n"
        "fn consumer() { public(); }\n"
    )
    open_nova(server, consumer_uri, consumer)

    rename = server.handle(
        request(
            "textDocument/rename",
            30,
            {
                "textDocument": {"uri": api_uri},
                "position": position(api, "public", delta=1),
                "newName": "renamed_public",
            },
        )
    )
    assert rename is not None
    changes = rename["result"]["changes"]
    assert set(changes) == {api_uri, consumer_uri}
    assert facade_uri not in changes
    assert [edit["newText"] for edit in changes[api_uri]] == [
        "renamed_public",
        "renamed_public",
    ]
    assert [edit["newText"] for edit in changes[consumer_uri]] == [
        "renamed_public"
    ]


def test_wildcard_reexport_reports_unresolved_target_and_cycles() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    missing_uri = "file:///workspace/missing-export.nova"
    open_nova(
        server,
        missing_uri,
        "export * from ./absent.nova;\n",
    )
    assert "nova.unresolved-export-target" in diagnostic_codes(server, missing_uri)

    left_uri = "file:///workspace/left-export.nova"
    right_uri = "file:///workspace/right-export.nova"
    open_nova(
        server,
        left_uri,
        "export * from ./right-export.nova;\n",
    )
    open_nova(
        server,
        right_uri,
        "export * from ./left-export.nova;\n",
    )
    assert "nova.import-cycle" in diagnostic_codes(server, left_uri)
    assert "nova.import-cycle" in diagnostic_codes(server, right_uri)


def test_wildcard_reexport_has_document_link(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path, document_links=True)
    facade_uri = (tmp_path / "facade.nova").as_uri()
    source = "export * from ./provider.nova;\n"
    open_nova(server, facade_uri, source)

    response = server.handle(
        request(
            "textDocument/documentLink",
            40,
            {"textDocument": {"uri": facade_uri}},
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 40,
        "result": [
            {
                "range": {
                    "start": {"line": 0, "character": 14},
                    "end": {"line": 0, "character": 29},
                },
                "target": provider.as_uri(),
            }
        ],
    }


def test_closed_wildcard_reexport_path_rewrites_with_null_version(
    tmp_path: Path,
) -> None:
    facade = tmp_path / "facade.nova"
    provider = tmp_path / "provider.nova"
    renamed = tmp_path / "renamed.nova"
    facade.write_text(
        "export * from ./provider.nova;\n",
        encoding="utf-8",
    )
    provider.write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(
        server,
        folder=tmp_path,
        will_rename=True,
        document_changes=True,
    )

    response = server.handle(
        request(
            "workspace/willRenameFiles",
            50,
            {
                "files": [
                    {
                        "oldUri": provider.as_uri(),
                        "newUri": renamed.as_uri(),
                    }
                ]
            },
        )
    )
    assert response is not None
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": facade.as_uri(), "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": 14},
                        "end": {"line": 0, "character": 29},
                    },
                    "newText": "./renamed.nova",
                }
            ],
        }
    ]
