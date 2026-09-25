from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.nova import NovaFunctionSyntax


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    folders: list[dict[str, str]],
    *,
    document_links: bool = False,
    will_rename: bool = False,
    document_changes: bool = False,
    module_search_roots: list[str] | None = None,
    configuration: bool = False,
    dynamic_configuration_registration: bool = False,
    diagnostic_refresh: bool = False,
    watched_files: bool = False,
) -> None:
    text_document: dict[str, Any] = {"completion": {}}
    if document_links:
        text_document["documentLink"] = {}
    workspace: dict[str, Any] = {
        "workspaceFolders": True,
        "configuration": configuration,
    }
    if dynamic_configuration_registration:
        workspace["didChangeConfiguration"] = {"dynamicRegistration": True}
    if diagnostic_refresh:
        workspace["diagnostics"] = {"refreshSupport": True}
    if watched_files:
        workspace["didChangeWatchedFiles"] = {"dynamicRegistration": True}
    if will_rename:
        workspace["fileOperations"] = {"willRename": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": text_document,
            "workspace": workspace,
        },
        "workspaceFolders": folders,
    }
    if module_search_roots is not None:
        params["initializationOptions"] = {
            "nova": {"moduleSearchRoots": module_search_roots}
        }
    response = server.handle(
        request(
            "initialize",
            1,
            params,
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))


def module_configuration_result(
    configuration_request: dict[str, Any],
    roots: list[str] | None,
) -> list[Any]:
    items = configuration_request["params"]["items"]
    assert items[-1] == {
        "section": "mini-language-server.nova.moduleSearchRoots"
    }
    return [None] * (len(items) - 1) + [roots]


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


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {
        item.code
        for item in snapshot.diagnostics
        if item.code is not None
    }


def test_parser_accepts_bare_paths_for_all_module_edges() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import pkg/dep.nova;\n"
        "import { target } from api/provider.nova;\n"
        "import * as sdk from modules/provider.nova;\n"
        "export * from facade/provider.nova;\n"
        "fn main() {}\n"
    )

    tree = server.nova_adapter.parse(text)

    assert isinstance(tree, NovaFunctionSyntax)
    assert [item.path for item in tree.imports] == [
        "pkg/dep.nova",
        "api/provider.nova",
        "modules/provider.nova",
    ]
    assert tree.imports[1].has_name_list is True
    assert tree.imports[2].namespace == "sdk"
    assert [item.path for item in tree.wildcard_exports] == [
        "facade/provider.nova"
    ]


def test_unique_bare_module_resolves_closed_cross_folder_target_and_link(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    package = shared / "pkg"
    app.mkdir()
    package.mkdir(parents=True)
    provider = package / "provider.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    caller = app / "main.nova"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        document_links=True,
    )
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(); }\n"
    )
    open_nova(server, caller.as_uri(), source)

    assert "nova.unresolved-import" not in diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-function" not in diagnostic_codes(server, caller.as_uri())
    assert server.documents.get(provider.as_uri()) is None

    definition = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": caller.as_uri()},
                "position": {
                    "line": 1,
                    "character": source.splitlines()[1].index("target") + 1,
                },
            },
        )
    )
    assert definition is not None
    assert definition["result"]["uri"] == provider.as_uri()

    links = server.handle(
        request(
            "textDocument/documentLink",
            11,
            {"textDocument": {"uri": caller.as_uri()}},
        )
    )
    assert links is not None
    assert links["result"] == [
        {
            "range": {
                "start": {"line": 0, "character": 23},
                "end": {"line": 0, "character": 40},
            },
            "target": provider.as_uri(),
        }
    ]


def test_bare_module_lookup_is_ambiguous_across_workspace_roots(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        package = root / "pkg"
        package.mkdir()
        (package / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
        document_links=True,
    )
    caller = app / "main.nova"
    source = "import pkg/provider.nova;\nfn caller() { target(); }\n"
    open_nova(server, caller.as_uri(), source)

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.ambiguous-import" in codes
    assert "nova.unresolved-import" not in codes
    assert "nova.unresolved-function" in codes

    snapshot = server.diagnostics.get(caller.as_uri())
    assert snapshot is not None
    diagnostic = next(
        item
        for item in snapshot.diagnostics
        if item.code == "nova.ambiguous-import"
    )
    assert [item.uri for item in diagnostic.related_information] == [
        (left / "pkg" / "provider.nova").as_uri(),
        (right / "pkg" / "provider.nova").as_uri(),
    ]
    assert all(
        item.span.start == 0 and item.span.end == 0
        for item in diagnostic.related_information
    )
    assert all(
        item.semantic is None
        for item in diagnostic.related_information
    )

    links = server.handle(
        request(
            "textDocument/documentLink",
            12,
            {"textDocument": {"uri": caller.as_uri()}},
        )
    )
    assert links == {"jsonrpc": "2.0", "id": 12, "result": []}


def test_bare_import_completion_lists_only_uniquely_resolved_modules(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    (shared / "pkg").mkdir(parents=True)
    target = shared / "pkg" / "provider.nova"
    target.write_text("fn target() {}\n", encoding="utf-8")
    main = app / "main.nova"
    text = "import pkg/pro"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    open_nova(server, main.as_uri(), text)

    response = server.handle(
        request(
            "textDocument/completion",
            20,
            {
                "textDocument": {"uri": main.as_uri()},
                "position": {"line": 0, "character": len(text)},
            },
        )
    )
    assert response is not None
    assert [item["label"] for item in response["result"]] == [
        "pkg/provider.nova"
    ]


def test_bare_import_completion_suppresses_ambiguous_module_label(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )
    main = app / "main.nova"
    text = "import pkg/pro"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
    )
    open_nova(server, main.as_uri(), text)

    response = server.handle(
        request(
            "textDocument/completion",
            21,
            {
                "textDocument": {"uri": main.as_uri()},
                "position": {"line": 0, "character": len(text)},
            },
        )
    )
    assert response is not None
    assert response["result"] == []


def test_bare_module_edges_participate_in_exact_cycle_diagnostics(
    tmp_path: Path,
) -> None:
    left_root = tmp_path / "left"
    right_root = tmp_path / "right"
    (left_root / "pkg").mkdir(parents=True)
    (right_root / "pkg").mkdir(parents=True)
    left = left_root / "pkg" / "left.nova"
    right = right_root / "pkg" / "right.nova"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": left_root.as_uri(), "name": "left"},
            {"uri": right_root.as_uri(), "name": "right"},
        ],
    )
    open_nova(
        server,
        left.as_uri(),
        "import pkg/right.nova;\nfn left() {}\n",
    )
    open_nova(
        server,
        right.as_uri(),
        "import pkg/left.nova;\nfn right() {}\n",
    )

    assert "nova.import-cycle" in diagnostic_codes(server, left.as_uri())
    assert "nova.import-cycle" in diagnostic_codes(server, right.as_uri())


def test_will_rename_preserves_unique_bare_module_spelling(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    (shared / "pkg").mkdir(parents=True)
    provider = shared / "pkg" / "provider.nova"
    renamed = shared / "pkg" / "renamed.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    caller = app / "main.nova"
    source = "import pkg/provider.nova;\nfn caller() { target(); }\n"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        will_rename=True,
        document_changes=True,
    )
    open_nova(server, caller.as_uri(), source, version=7)

    response = server.handle(
        request(
            "workspace/willRenameFiles",
            30,
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
            "textDocument": {"uri": caller.as_uri(), "version": 7},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 24},
                    },
                    "newText": "pkg/renamed.nova",
                }
            ],
        }
    ]


def test_closed_bare_ambiguity_surfaces_related_documents(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )
    caller = app / "main.nova"
    caller.write_text(
        "import pkg/provider.nova;\nfn caller() { target(); }\n",
        encoding="utf-8",
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
    )

    response = server.handle(
        request(
            "textDocument/diagnostic",
            40,
            {"textDocument": {"uri": caller.as_uri()}},
        )
    )

    assert response is not None
    ambiguity = next(
        item
        for item in response["result"]["items"]
        if item["code"] == "nova.ambiguous-import"
    )
    assert ambiguity["message"] == "ambiguous import 'pkg/provider.nova'"
    related = response["result"]["relatedDocuments"]
    assert list(related) == [
        (left / "pkg" / "provider.nova").as_uri(),
        (right / "pkg" / "provider.nova").as_uri(),
    ]
    assert all(report["kind"] == "full" for report in related.values())


def test_bare_wildcard_export_reports_ambiguous_target(
    tmp_path: Path,
) -> None:
    facade = tmp_path / "facade"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (facade, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": facade.as_uri(), "name": "facade"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
    )
    module = facade / "module.nova"
    open_nova(
        server,
        module.as_uri(),
        "export * from pkg/provider.nova;\nfn facade() {}\n",
    )

    codes = diagnostic_codes(server, module.as_uri())
    assert "nova.ambiguous-export-target" in codes
    assert "nova.unresolved-export-target" not in codes
    snapshot = server.diagnostics.get(module.as_uri())
    assert snapshot is not None
    diagnostic = next(
        item
        for item in snapshot.diagnostics
        if item.code == "nova.ambiguous-export-target"
    )
    assert [item.uri for item in diagnostic.related_information] == [
        (left / "pkg" / "provider.nova").as_uri(),
        (right / "pkg" / "provider.nova").as_uri(),
    ]


def test_configured_bare_module_search_root_order_selects_first_match(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root, body in (
        (left, "fn target() { return 1; }\n"),
        (right, "fn target() { return 2; }\n"),
    ):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(body, encoding="utf-8")

    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(); }\n"
    )
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
        module_search_roots=[right.as_uri(), left.as_uri()],
    )
    open_nova(server, caller.as_uri(), source)

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.ambiguous-import" not in codes
    assert "nova.unresolved-import" not in codes
    assert "nova.unresolved-function" not in codes

    snapshots = server.workspace_symbols.snapshots()
    resolution = server._nova_import_resolution(
        caller.as_uri(),
        "pkg/provider.nova",
        snapshots=snapshots,
    )
    assert resolution.status == "resolved"
    assert resolution.target_uri == (right / "pkg" / "provider.nova").as_uri()
    assert resolution.candidate_uris == (
        (right / "pkg" / "provider.nova").as_uri(),
        (left / "pkg" / "provider.nova").as_uri(),
    )

    definition = server.handle(
        request(
            "textDocument/definition",
            60,
            {
                "textDocument": {"uri": caller.as_uri()},
                "position": {
                    "line": 1,
                    "character": source.splitlines()[1].index("target") + 1,
                },
            },
        )
    )
    assert definition is not None
    assert definition["result"]["uri"] == (
        right / "pkg" / "provider.nova"
    ).as_uri()


def test_configured_bare_module_search_roots_filter_unlisted_folders(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    listed = tmp_path / "listed"
    hidden = tmp_path / "hidden"
    for root in (app, listed, hidden):
        root.mkdir()
    (hidden / "pkg").mkdir()
    (hidden / "pkg" / "provider.nova").write_text(
        "fn target() {}\n",
        encoding="utf-8",
    )
    caller = app / "main.nova"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": listed.as_uri(), "name": "listed"},
            {"uri": hidden.as_uri(), "name": "hidden"},
        ],
        module_search_roots=[listed.as_uri()],
    )
    open_nova(
        server,
        caller.as_uri(),
        "import pkg/provider.nova;\nfn caller() { target(); }\n",
    )

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" in codes
    assert "nova.ambiguous-import" not in codes
    assert "nova.unresolved-function" in codes


def test_configured_bare_module_search_roots_restore_completion_for_precedence(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )
    main = app / "main.nova"
    text = "import pkg/pro"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
        module_search_roots=[right.as_uri(), left.as_uri()],
    )
    open_nova(server, main.as_uri(), text)

    response = server.handle(
        request(
            "textDocument/completion",
            61,
            {
                "textDocument": {"uri": main.as_uri()},
                "position": {"line": 0, "character": len(text)},
            },
        )
    )
    assert response is not None
    assert [item["label"] for item in response["result"]] == [
        "pkg/provider.nova"
    ]


def test_configured_module_root_survives_workspace_folder_lifecycle(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    (shared / "pkg").mkdir(parents=True)
    provider = shared / "pkg" / "provider.nova"
    provider.write_text(
        "fn target() {}\n",
        encoding="utf-8",
    )
    caller = app / "main.nova"
    source = "import pkg/provider.nova;\nfn caller() { target(); }\n"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        module_search_roots=[shared.as_uri()],
    )
    open_nova(server, caller.as_uri(), source)

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" not in codes
    assert "nova.unresolved-function" not in codes

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [],
                    "removed": [{"uri": shared.as_uri(), "name": "shared"}],
                }
            },
        )
    )
    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" not in codes
    assert "nova.unresolved-function" not in codes
    assert server.workspace_symbols.get(provider.as_uri()) is not None
    assert server.workspace_folders.contains(provider.as_uri()) is False

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": shared.as_uri(), "name": "shared"}],
                    "removed": [],
                }
            },
        )
    )
    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" not in codes
    assert "nova.unresolved-function" not in codes


def test_remote_file_module_search_root_fails_closed(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    (shared / "pkg").mkdir(parents=True)
    (shared / "pkg" / "provider.nova").write_text(
        "fn target() {}\n",
        encoding="utf-8",
    )
    caller = app / "main.nova"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        module_search_roots=["file://remote-host/workspace"],
    )
    open_nova(
        server,
        caller.as_uri(),
        "import pkg/provider.nova;\nfn caller() { target(); }\n",
    )

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" in codes
    assert "nova.unresolved-function" in codes


def test_invalid_explicit_module_search_roots_fail_closed(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    (shared / "pkg").mkdir(parents=True)
    (shared / "pkg" / "provider.nova").write_text(
        "fn target() {}\n",
        encoding="utf-8",
    )
    caller = app / "main.nova"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        module_search_roots=["https://example.com/not-a-workspace-root"],
    )
    open_nova(
        server,
        caller.as_uri(),
        "import pkg/provider.nova;\nfn caller() { target(); }\n",
    )

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" in codes
    assert "nova.unresolved-function" in codes


def test_workspace_configuration_overrides_module_search_root_order(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )
    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
        module_search_roots=[left.as_uri(), right.as_uri()],
        configuration=True,
    )
    open_nova(server, caller.as_uri(), source)

    snapshots = server.workspace_symbols.snapshots()
    initial = server._nova_import_resolution(
        caller.as_uri(),
        "pkg/provider.nova",
        snapshots=snapshots,
    )
    assert initial.target_uri == (left / "pkg" / "provider.nova").as_uri()

    requests = server.drain_server_requests()
    assert len(requests) == 1
    config = requests[0]
    assert config["method"] == "workspace/configuration"
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": config["id"],
            "result": module_configuration_result(
                config,
                [right.as_uri(), left.as_uri()],
            ),
        }
    ) is None

    updated = server._nova_import_resolution(
        caller.as_uri(),
        "pkg/provider.nova",
        snapshots=server.workspace_symbols.snapshots(),
    )
    assert updated.target_uri == (right / "pkg" / "provider.nova").as_uri()
    assert updated.candidate_uris == (
        (right / "pkg" / "provider.nova").as_uri(),
        (left / "pkg" / "provider.nova").as_uri(),
    )
    assert "nova.unresolved-import" not in diagnostic_codes(server, caller.as_uri())
    assert "nova.ambiguous-import" not in diagnostic_codes(server, caller.as_uri())


def test_configuration_change_supersedes_module_root_generation(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )
    caller = app / "main.nova"
    source = "import pkg/provider.nova;\nfn caller() { target(); }\n"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
        module_search_roots=[left.as_uri(), right.as_uri()],
        configuration=True,
    )
    open_nova(server, caller.as_uri(), source)
    first = server.drain_server_requests()[0]
    server.drain_notifications()

    server.handle(
        notify(
            "workspace/didChangeConfiguration",
            {"settings": {"nova": {"moduleSearchRoots": ["ignored"]}}},
        )
    )

    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {"id": first["id"]},
        }
    ]
    second_requests = server.drain_server_requests()
    assert len(second_requests) == 1
    second = second_requests[0]

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": first["id"],
            "result": module_configuration_result(
                first,
                [right.as_uri(), left.as_uri()],
            ),
        }
    ) is None
    still_initial = server._nova_import_resolution(
        caller.as_uri(),
        "pkg/provider.nova",
        snapshots=server.workspace_symbols.snapshots(),
    )
    assert still_initial.target_uri == (left / "pkg" / "provider.nova").as_uri()

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": second["id"],
            "result": module_configuration_result(second, []),
        }
    ) is None
    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" in codes
    assert "nova.unresolved-function" in codes


def test_dynamic_module_root_registration_uses_distinct_section(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": root.as_uri(), "name": "workspace"}],
        module_search_roots=[root.as_uri()],
        configuration=True,
        dynamic_configuration_registration=True,
    )

    requests = server.drain_server_requests()
    assert [item["method"] for item in requests] == [
        "client/registerCapability",
        "workspace/configuration",
    ]
    registrations = requests[0]["params"]["registrations"]
    assert [item["registerOptions"]["section"] for item in registrations] == [
        "mini-language-server.formatting",
        "mini-language-server.nova.moduleSearchRoots",
    ]


def test_module_policy_change_invalidates_inflight_definition(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (app, left, right):
        root.mkdir()
    for root in (left, right):
        (root / "pkg").mkdir()
        (root / "pkg" / "provider.nova").write_text(
            "fn target() {}\n",
            encoding="utf-8",
        )
    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "left"},
            {"uri": right.as_uri(), "name": "right"},
        ],
        module_search_roots=[left.as_uri(), right.as_uri()],
        configuration=True,
    )
    open_nova(server, caller.as_uri(), source)
    initial_config = server.drain_server_requests()[0]
    server.handle(
        {
            "jsonrpc": "2.0",
            "id": initial_config["id"],
            "result": module_configuration_result(
                initial_config,
                [left.as_uri(), right.as_uri()],
            ),
        }
    )

    server.handle(notify("workspace/didChangeConfiguration", {"settings": {}}))
    update = server.drain_server_requests()[0]
    real_commit = server.workspace_symbols.commit_snapshots_if_current
    injected = False

    def switch_policy_before_commit(snapshots, callback):
        nonlocal injected
        if not injected:
            injected = True
            server.workspace_symbols.commit_snapshots_if_current = real_commit
            server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": update["id"],
                    "result": module_configuration_result(
                        update,
                        [right.as_uri(), left.as_uri()],
                    ),
                }
            )
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = (
        switch_policy_before_commit
    )  # type: ignore[method-assign]

    response = server.handle(
        request(
            "textDocument/definition",
            90,
            {
                "textDocument": {"uri": caller.as_uri()},
                "position": {
                    "line": 1,
                    "character": source.splitlines()[1].index("target") + 1,
                },
            },
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 90,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_external_local_module_root_resolves_closed_provider(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    external = tmp_path / "external"
    app.mkdir()
    (external / "pkg").mkdir(parents=True)
    provider = external / "pkg" / "provider.nova"
    provider.write_text("fn target(value: Int) {}\n", encoding="utf-8")
    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(1); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": app.as_uri(), "name": "app"}],
        module_search_roots=[external.as_uri()],
    )
    open_nova(server, caller.as_uri(), source)

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" not in codes
    assert "nova.ambiguous-import" not in codes
    assert "nova.unresolved-function" not in codes
    assert "nova.argument-count" not in codes
    assert server.documents.get(provider.as_uri()) is None
    assert server.workspace_symbols.get(provider.as_uri()) is not None

    definition = server.handle(
        request(
            "textDocument/definition",
            100,
            {
                "textDocument": {"uri": caller.as_uri()},
                "position": {
                    "line": 1,
                    "character": source.splitlines()[1].index("target") + 1,
                },
            },
        )
    )
    assert definition is not None
    assert definition["result"]["uri"] == provider.as_uri()


def test_external_local_module_root_participates_in_import_completion(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    external = tmp_path / "external"
    app.mkdir()
    (external / "pkg").mkdir(parents=True)
    (external / "pkg" / "provider.nova").write_text(
        "fn target() {}\n",
        encoding="utf-8",
    )
    main = app / "main.nova"
    text = "import pkg/pro"

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": app.as_uri(), "name": "app"}],
        module_search_roots=[external.as_uri()],
    )
    open_nova(server, main.as_uri(), text)

    response = server.handle(
        request(
            "textDocument/completion",
            101,
            {
                "textDocument": {"uri": main.as_uri()},
                "position": {"line": 0, "character": len(text)},
            },
        )
    )
    assert response is not None
    assert [item["label"] for item in response["result"]] == [
        "pkg/provider.nova"
    ]


def test_external_module_open_buffer_takes_over_and_close_restores_disk(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    external = tmp_path / "external"
    app.mkdir()
    (external / "pkg").mkdir(parents=True)
    provider = external / "pkg" / "provider.nova"
    provider.write_text("fn target(value: Int) {}\n", encoding="utf-8")
    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(1); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": app.as_uri(), "name": "app"}],
        module_search_roots=[external.as_uri()],
    )
    open_nova(server, caller.as_uri(), source)
    assert "nova.argument-count" not in diagnostic_codes(
        server,
        caller.as_uri(),
    )

    open_nova(
        server,
        provider.as_uri(),
        "fn target() {}\n",
    )
    assert server.documents.get(provider.as_uri()) is not None
    assert "nova.argument-count" in diagnostic_codes(
        server,
        caller.as_uri(),
    )

    server.handle(
        notify(
            "textDocument/didClose",
            {"textDocument": {"uri": provider.as_uri()}},
        )
    )
    assert server.documents.get(provider.as_uri()) is None
    restored = server.workspace_symbols.get(provider.as_uri())
    assert restored is not None
    assert restored.symbols.syntax.document.text == "fn target(value: Int) {}\n"
    assert "nova.argument-count" not in diagnostic_codes(
        server,
        caller.as_uri(),
    )


def test_runtime_module_root_switch_replaces_external_provider_set(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    app.mkdir()
    for root, body in (
        (left, "fn target(value: Int) {}\n"),
        (right, "fn target() {}\n"),
    ):
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "provider.nova").write_text(body, encoding="utf-8")
    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(1); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": app.as_uri(), "name": "app"}],
        module_search_roots=[left.as_uri()],
        configuration=True,
    )
    open_nova(server, caller.as_uri(), source)
    assert "nova.argument-count" not in diagnostic_codes(
        server,
        caller.as_uri(),
    )
    assert server.workspace_symbols.get(
        (left / "pkg" / "provider.nova").as_uri()
    ) is not None
    assert server.workspace_symbols.get(
        (right / "pkg" / "provider.nova").as_uri()
    ) is None

    configuration = server.drain_server_requests()[0]
    assert configuration["method"] == "workspace/configuration"
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": configuration["id"],
            "result": module_configuration_result(
                configuration,
                [right.as_uri()],
            ),
        }
    ) is None

    assert server.workspace_symbols.get(
        (left / "pkg" / "provider.nova").as_uri()
    ) is None
    assert server.workspace_symbols.get(
        (right / "pkg" / "provider.nova").as_uri()
    ) is not None
    assert "nova.argument-count" in diagnostic_codes(
        server,
        caller.as_uri(),
    )


def test_external_provider_can_resolve_bare_imports_inside_provider_graph(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    external = tmp_path / "external"
    app.mkdir()
    (external / "pkg").mkdir(parents=True)
    core = external / "pkg" / "core.nova"
    core.write_text("fn target() {}\n", encoding="utf-8")
    facade = external / "facade.nova"
    facade.write_text(
        "import { target } from pkg/core.nova;\n"
        "export { target };\n",
        encoding="utf-8",
    )
    caller = app / "main.nova"
    source = (
        "import { target } from facade.nova;\n"
        "fn caller() { target(); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": app.as_uri(), "name": "app"}],
        module_search_roots=[external.as_uri()],
    )
    open_nova(server, caller.as_uri(), source)

    codes = diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-import" not in codes
    assert "nova.unresolved-import-name" not in codes
    assert "nova.unresolved-function" not in codes

    definition = server.handle(
        request(
            "textDocument/definition",
            102,
            {
                "textDocument": {"uri": caller.as_uri()},
                "position": {
                    "line": 1,
                    "character": source.splitlines()[1].index("target") + 1,
                },
            },
        )
    )
    assert definition is not None
    assert definition["result"]["uri"] == core.as_uri()


def test_reported_external_module_change_refreshes_detached_provider(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    external = tmp_path / "external"
    app.mkdir()
    (external / "pkg").mkdir(parents=True)
    provider = external / "pkg" / "provider.nova"
    provider.write_text("fn target(value: Int) {}\n", encoding="utf-8")
    caller = app / "main.nova"
    source = (
        "import { target } from pkg/provider.nova;\n"
        "fn caller() { target(1); }\n"
    )

    server = NovaProductLanguageServer()
    initialize(
        server,
        [{"uri": app.as_uri(), "name": "app"}],
        module_search_roots=[external.as_uri()],
        watched_files=True,
    )
    registration = server.drain_server_requests()
    assert len(registration) == 1
    assert registration[0]["method"] == "client/registerCapability"
    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": registration[0]["id"],
            "result": None,
        }
    ) is None

    open_nova(server, caller.as_uri(), source)
    assert "nova.argument-count" not in diagnostic_codes(
        server,
        caller.as_uri(),
    )

    provider.write_text("fn target() {}\n", encoding="utf-8")
    server.handle(
        notify(
            "workspace/didChangeWatchedFiles",
            {
                "changes": [
                    {"uri": provider.as_uri(), "type": 2},
                ]
            },
        )
    )

    assert "nova.argument-count" in diagnostic_codes(
        server,
        caller.as_uri(),
    )
    current = server.workspace_symbols.get(provider.as_uri())
    assert current is not None
    assert current.symbols.syntax.document.text == "fn target() {}\n"
