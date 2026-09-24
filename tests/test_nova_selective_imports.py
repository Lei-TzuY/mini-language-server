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
                "position": position(caller, "visible();", delta=1),
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

def test_selective_import_alias_parser_tracks_source_and_binding_spans() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import { source as local, plain } from ./provider.nova;\n"
        "fn main() {}\n"
    )

    tree = server.nova_adapter.parse(text)

    assert len(tree.imports) == 1
    imported = tree.imports[0]
    assert [item.name for item in imported.names] == ["source", "plain"]
    assert [item.binding_name for item in imported.names] == ["local", "plain"]
    first, second = imported.names
    assert first.alias == "local"
    assert first.alias_span is not None
    assert text[first.span.start : first.span.end] == "source"
    assert text[first.alias_span.start : first.alias_span.end] == "local"
    assert second.alias is None
    assert second.alias_span is None
    assert second.binding_span == second.span


def test_selective_import_alias_resolves_definition_completion_and_references() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    canonical_uri = "file:///workspace/canonical.nova"
    alias_uri = "file:///workspace/alias.nova"
    provider = "fn source() {}\n"
    canonical = (
        "import { source } from ./provider.nova;\n"
        "fn canonical() { source(); }\n"
    )
    aliased = (
        "import { source as local } from ./provider.nova;\n"
        "fn aliased() { local(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, canonical_uri, canonical)
    open_nova(server, alias_uri, aliased)

    assert "nova.unresolved-function" not in diagnostic_codes(server, alias_uri)

    definition = server.handle(
        request(
            "textDocument/definition",
            50,
            {
                "textDocument": {"uri": alias_uri},
                "position": position(aliased, "local();", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == provider_uri

    completion = server.handle(
        request(
            "textDocument/completion",
            51,
            {
                "textDocument": {"uri": alias_uri},
                "position": position(aliased, "local();"),
            },
        )
    )
    assert completion is not None
    labels = {item["label"] for item in completion["result"]}
    assert "local" in labels
    assert "source" not in labels

    references = server.handle(
        request(
            "textDocument/references",
            52,
            {
                "textDocument": {"uri": alias_uri},
                "position": position(aliased, "local();", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["uri"] for item in references["result"]] == [
        alias_uri,
        canonical_uri,
        provider_uri,
    ]


def test_selective_import_alias_reports_duplicate_local_binding() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn left() {}\nfn right() {}\n")
    caller = (
        "import { left as shared, right as shared } from ./provider.nova;\n"
        "fn caller() { shared(); }\n"
    )
    open_nova(server, caller_uri, caller)

    codes = diagnostic_codes(server, caller_uri)
    assert "nova.duplicate-import-name" in codes
    assert "nova.ambiguous-function" in codes


def test_selective_import_alias_can_be_reexported_without_changing_canonical_identity() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    open_nova(
        server,
        middle_uri,
        (
            "import { source as local } from ./provider.nova;\n"
            "export { local };\n"
        ),
    )
    root = "import ./middle.nova;\nfn root() { local(); }\n"
    open_nova(server, root_uri, root)

    assert "nova.unresolved-export" not in diagnostic_codes(server, middle_uri)
    definition = server.handle(
        request(
            "textDocument/definition",
            53,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "local();", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == provider_uri


def test_canonical_rename_updates_alias_source_selector_but_preserves_alias_call() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    alias_uri = "file:///workspace/alias.nova"
    canonical_uri = "file:///workspace/canonical.nova"
    provider = "fn source() {}\nexport { source };\n"
    aliased = (
        "import { source as local } from ./provider.nova;\n"
        "fn aliased() { local(); }\n"
    )
    canonical = (
        "import { source } from ./provider.nova;\n"
        "fn canonical() { source(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, alias_uri, aliased)
    open_nova(server, canonical_uri, canonical)

    renamed = server.handle(
        request(
            "textDocument/rename",
            54,
            {
                "textDocument": {"uri": provider_uri},
                "position": position(provider, "source", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    changes = renamed["result"]["changes"]

    provider_edits = changes[provider_uri]
    assert [provider[server._source_text(provider).offset_at(
        server._position_from_lsp(edit["range"]["start"])
    ):server._source_text(provider).offset_at(
        server._position_from_lsp(edit["range"]["end"])
    )] for edit in provider_edits] == ["source", "source"]

    alias_edits = changes[alias_uri]
    assert len(alias_edits) == 1
    assert alias_edits[0]["newText"] == "renamed"
    alias_range = alias_edits[0]["range"]
    alias_start = server._source_text(aliased).offset_at(
        server._position_from_lsp(alias_range["start"])
    )
    alias_end = server._source_text(aliased).offset_at(
        server._position_from_lsp(alias_range["end"])
    )
    assert aliased[alias_start:alias_end] == "source"
    assert "local" not in [edit["newText"] for edit in alias_edits]

    canonical_edits = changes[canonical_uri]
    assert len(canonical_edits) == 2
    assert all(edit["newText"] == "renamed" for edit in canonical_edits)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            55,
            {
                "textDocument": {"uri": alias_uri},
                "position": position(aliased, "local();", delta=1),
            },
        )
    )
    assert prepared is not None
    assert prepared["result"] is None

    alias_rename = server.handle(
        request(
            "textDocument/rename",
            56,
            {
                "textDocument": {"uri": alias_uri},
                "position": position(aliased, "local();", delta=1),
                "newName": "other",
            },
        )
    )
    assert alias_rename is not None
    assert alias_rename["result"] is None


def test_closed_importer_obeys_selective_alias_binding(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn source() {}\n", encoding="utf-8")
    caller.write_text(
        (
            "import { source as local } from ./provider.nova;\n"
            "fn caller() { local(); }\n"
        ),
        encoding="utf-8",
    )
    server = initialized_workspace_server(tmp_path)
    caller_uri = caller.as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            57,
            {"textDocument": {"uri": caller_uri}},
        )
    )

    assert response is not None
    assert all(
        item["code"] != "nova.unresolved-function"
        for item in response["result"]["items"]
    )
    assert server.documents.get(caller_uri) is None
    assert server.diagnostics.get(caller_uri) is None
