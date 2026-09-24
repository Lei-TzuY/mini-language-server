from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.nova import NovaFunctionAdapter


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
    assert provider_edits == [
        {
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 9},
            },
            "newText": "renamed",
        },
        {
            "range": {
                "start": {"line": 1, "character": 9},
                "end": {"line": 1, "character": 15},
            },
            "newText": "renamed",
        },
    ]

    alias_edits = changes[alias_uri]
    assert alias_edits == [
        {
            "range": {
                "start": {"line": 0, "character": 9},
                "end": {"line": 0, "character": 15},
            },
            "newText": "renamed",
        }
    ]

    canonical_edits = changes[canonical_uri]
    assert canonical_edits == [
        {
            "range": {
                "start": {"line": 0, "character": 9},
                "end": {"line": 0, "character": 15},
            },
            "newText": "renamed",
        },
        {
            "range": {
                "start": {"line": 1, "character": 17},
                "end": {"line": 1, "character": 23},
            },
            "newText": "renamed",
        },
    ]

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

def test_selective_import_preserves_t_prefixed_selector_and_alias() -> None:
    syntax = NovaFunctionAdapter.parse(
        "import { transit as target } from ./provider.nova;\n"
    )

    assert len(syntax.imports) == 1
    selected = syntax.imports[0].names
    assert len(selected) == 1
    assert selected[0].name == "transit"
    assert selected[0].alias == "target"
    assert selected[0].binding_name == "target"



def test_private_alias_prepare_and_rename_stays_importer_local() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    importer_uri = "file:///workspace/importer.nova"
    provider = "fn source() {}\n"
    importer = (
        "import { source as local } from ./provider.nova;\n"
        "export {};\n"
        "fn caller() { local(); local(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, importer_uri, importer)

    alias_position = position(importer, "local }", delta=1)
    alias_prepare = server.handle(
        request(
            "textDocument/prepareRename",
            80,
            {
                "textDocument": {"uri": importer_uri},
                "position": alias_position,
            },
        )
    )
    assert alias_prepare is not None
    assert alias_prepare["result"]["placeholder"] == "local"

    call_position = position(importer, "local();", delta=1)
    call_prepare = server.handle(
        request(
            "textDocument/prepareRename",
            81,
            {
                "textDocument": {"uri": importer_uri},
                "position": call_position,
            },
        )
    )
    assert call_prepare is not None
    assert call_prepare["result"]["placeholder"] == "local"

    renamed = server.handle(
        request(
            "textDocument/rename",
            82,
            {
                "textDocument": {"uri": importer_uri},
                "position": call_position,
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    changes = renamed["result"]["changes"]
    assert list(changes) == [importer_uri]
    edits = changes[importer_uri]
    assert [edit["newText"] for edit in edits] == [
        "renamed",
        "renamed",
        "renamed",
    ]

    first_line = importer.splitlines()[0]
    alias_start = first_line.index("local")
    call_line = importer.splitlines()[2]
    first_call = call_line.index("local")
    second_call = call_line.index("local", first_call + 1)
    assert [edit["range"] for edit in edits] == [
        {
            "start": {"line": 0, "character": alias_start},
            "end": {"line": 0, "character": alias_start + len("local")},
        },
        {
            "start": {"line": 2, "character": first_call},
            "end": {"line": 2, "character": first_call + len("local")},
        },
        {
            "start": {"line": 2, "character": second_call},
            "end": {"line": 2, "character": second_call + len("local")},
        },
    ]


def test_private_alias_rename_from_alias_syntax_uses_same_binding() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    importer_uri = "file:///workspace/importer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    importer = (
        "import { source as local } from ./provider.nova;\n"
        "export {};\n"
        "fn caller() { local(); }\n"
    )
    open_nova(server, importer_uri, importer)

    renamed = server.handle(
        request(
            "textDocument/rename",
            83,
            {
                "textDocument": {"uri": importer_uri},
                "position": position(importer, "local }", delta=1),
                "newName": "inside",
            },
        )
    )

    assert renamed is not None
    edits = renamed["result"]["changes"][importer_uri]
    assert [edit["newText"] for edit in edits] == ["inside", "inside"]


def test_private_alias_rename_rejects_importer_binding_collision() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    importer_uri = "file:///workspace/importer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    importer = (
        "import { source as local } from ./provider.nova;\n"
        "export {};\n"
        "fn taken() {}\n"
        "fn caller() { local(); }\n"
    )
    open_nova(server, importer_uri, importer)

    response = server.handle(
        request(
            "textDocument/rename",
            84,
            {
                "textDocument": {"uri": importer_uri},
                "position": position(importer, "local();", delta=1),
                "newName": "taken",
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 84,
        "error": {
            "code": -32803,
            "message": "Rename would conflict with existing binding 'taken'",
        },
    }


def test_outward_alias_rename_remains_fail_closed() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    importer_uri = "file:///workspace/importer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    importer = (
        "import { source as local } from ./provider.nova;\n"
        "fn caller() { local(); }\n"
    )
    open_nova(server, importer_uri, importer)
    call_position = position(importer, "local();", delta=1)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            85,
            {
                "textDocument": {"uri": importer_uri},
                "position": call_position,
            },
        )
    )
    assert prepared is not None
    assert prepared["result"] is None

    renamed = server.handle(
        request(
            "textDocument/rename",
            86,
            {
                "textDocument": {"uri": importer_uri},
                "position": call_position,
                "newName": "inside",
            },
        )
    )
    assert renamed is not None
    assert renamed["result"] is None


def test_private_alias_rename_rejects_workspace_drift() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    importer_uri = "file:///workspace/importer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    importer = (
        "import { source as local } from ./provider.nova;\n"
        "export {};\n"
        "fn caller() { local(); }\n"
    )
    open_nova(server, importer_uri, importer)

    original = server.workspace_symbols.get(provider_uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current
    replaced = False

    def replace_then_commit(snapshots, callback):
        nonlocal replaced
        if not replaced:
            replaced = True
            document = server.documents.get(provider_uri)
            assert document is not None
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]

    response = server.handle(
        request(
            "textDocument/rename",
            87,
            {
                "textDocument": {"uri": importer_uri},
                "position": position(importer, "local();", delta=1),
                "newName": "inside",
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 87,
        "error": {"code": -32801, "message": "Content modified"},
    }



def test_exported_alias_rename_propagates_explicit_selective_graph() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    leaf_uri = "file:///workspace/leaf.nova"
    provider = "fn source() {}\n"
    middle = (
        "import { source as public } from ./provider.nova;\n"
        "export { public };\n"
        "fn middle() { public(); }\n"
    )
    root = (
        "import { public } from ./middle.nova;\n"
        "export { public };\n"
        "fn root() { public(); }\n"
    )
    leaf = (
        "import { public as local } from ./root.nova;\n"
        "export {};\n"
        "fn leaf() { local(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, middle_uri, middle)
    open_nova(server, root_uri, root)
    open_nova(server, leaf_uri, leaf)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            100,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public }", delta=1),
            },
        )
    )
    assert prepared is not None
    assert prepared["result"]["placeholder"] == "public"

    renamed = server.handle(
        request(
            "textDocument/rename",
            101,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    changes = renamed["result"]["changes"]
    assert set(changes) == {middle_uri, root_uri, leaf_uri}
    assert [edit["newText"] for edit in changes[middle_uri]] == [
        "renamed",
        "renamed",
        "renamed",
    ]
    assert [edit["newText"] for edit in changes[root_uri]] == [
        "renamed",
        "renamed",
        "renamed",
    ]
    assert [edit["newText"] for edit in changes[leaf_uri]] == ["renamed"]
    assert provider_uri not in changes

    leaf_source_start = leaf.index("public")
    assert changes[leaf_uri][0]["range"] == {
        "start": {"line": 0, "character": leaf_source_start},
        "end": {"line": 0, "character": leaf_source_start + len("public")},
    }


def test_exported_alias_rename_rejects_bare_import_consumer() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    consumer_uri = "file:///workspace/consumer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    middle = (
        "import { source as public } from ./provider.nova;\n"
        "export { public };\n"
        "fn middle() { public(); }\n"
    )
    consumer = "import ./middle.nova;\nfn consumer() { public(); }\n"
    open_nova(server, middle_uri, middle)
    open_nova(server, consumer_uri, consumer)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            102,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
            },
        )
    )
    assert prepared is not None
    assert prepared["result"] is None

    renamed = server.handle(
        request(
            "textDocument/rename",
            103,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    assert renamed["result"] is None


def test_exported_alias_rename_rejects_implicit_downstream_export() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    consumer_uri = "file:///workspace/consumer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    middle = (
        "import { source as public } from ./provider.nova;\n"
        "export { public };\n"
        "fn middle() { public(); }\n"
    )
    consumer = (
        "import { public } from ./middle.nova;\n"
        "fn consumer() { public(); }\n"
    )
    open_nova(server, middle_uri, middle)
    open_nova(server, consumer_uri, consumer)

    renamed = server.handle(
        request(
            "textDocument/rename",
            104,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    assert renamed["result"] is None


def test_exported_alias_rename_rejects_downstream_binding_collision() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    consumer_uri = "file:///workspace/consumer.nova"
    open_nova(server, provider_uri, "fn source() {}\n")
    middle = (
        "import { source as public } from ./provider.nova;\n"
        "export { public };\n"
        "fn middle() { public(); }\n"
    )
    consumer = (
        "import { public } from ./middle.nova;\n"
        "export { public };\n"
        "fn renamed() {}\n"
        "fn consumer() { public(); }\n"
    )
    open_nova(server, middle_uri, middle)
    open_nova(server, consumer_uri, consumer)

    response = server.handle(
        request(
            "textDocument/rename",
            105,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 105,
        "error": {
            "code": -32803,
            "message": "Rename would conflict with existing binding 'renamed'",
        },
    }


def test_exported_alias_rename_versions_closed_downstream_as_null(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    middle_file = tmp_path / "middle.nova"
    root = tmp_path / "root.nova"
    leaf = tmp_path / "leaf.nova"
    provider.write_text("fn source() {}\n", encoding="utf-8")
    middle = (
        "import { source as public } from ./provider.nova;\n"
        "export { public };\n"
        "fn middle() { public(); }\n"
    )
    middle_file.write_text(middle, encoding="utf-8")
    root.write_text(
        (
            "import { public } from ./middle.nova;\n"
            "export { public };\n"
            "fn root() { public(); }\n"
        ),
        encoding="utf-8",
    )
    leaf.write_text(
        (
            "import { public as local } from ./root.nova;\n"
            "export {};\n"
            "fn leaf() { local(); }\n"
        ),
        encoding="utf-8",
    )
    server = initialized_workspace_server(
        tmp_path,
        document_changes=True,
    )
    middle_uri = middle_file.as_uri()
    open_nova(server, middle_uri, middle, version=7)

    response = server.handle(
        request(
            "textDocument/rename",
            106,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert response is not None
    changes = response["result"]["documentChanges"]
    versions = {
        item["textDocument"]["uri"]: item["textDocument"]["version"]
        for item in changes
    }
    assert versions[middle_uri] == 7
    assert versions[root.as_uri()] is None
    assert versions[leaf.as_uri()] is None
    assert provider.as_uri() not in versions


def test_exported_alias_rename_rejects_closed_graph_disk_drift(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    middle_file = tmp_path / "middle.nova"
    root = tmp_path / "root.nova"
    provider.write_text("fn source() {}\n", encoding="utf-8")
    middle = (
        "import { source as public } from ./provider.nova;\n"
        "export { public };\n"
        "fn middle() { public(); }\n"
    )
    middle_file.write_text(middle, encoding="utf-8")
    root.write_text(
        (
            "import { public } from ./middle.nova;\n"
            "export { public };\n"
            "fn root() { public(); }\n"
        ),
        encoding="utf-8",
    )
    server = initialized_workspace_server(tmp_path)
    middle_uri = middle_file.as_uri()
    open_nova(server, middle_uri, middle)

    original_checkpoint = server.requests.checkpoint
    calls = 0

    def mutate_before_publish(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            root.write_text(
                (
                    "import { public } from ./middle.nova;\n"
                    "export {};\n"
                    "fn root() { public(); }\n"
                ),
                encoding="utf-8",
            )

    server.requests.checkpoint = mutate_before_publish  # type: ignore[method-assign]

    response = server.handle(
        request(
            "textDocument/rename",
            107,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "public();", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 107,
        "error": {"code": -32801, "message": "Content modified"},
    }
