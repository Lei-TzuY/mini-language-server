from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    root: Path | None = None,
    will_rename: bool = False,
    document_changes: bool = False,
) -> None:
    workspace: dict[str, Any] = {"workspaceFolders": True}
    if will_rename:
        workspace["fileOperations"] = {"willRename": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": {
                "definition": {"linkSupport": True},
                "diagnostic": {},
            },
            "workspace": workspace,
        }
    }
    if root is not None:
        params["workspaceFolders"] = [{"uri": root.as_uri(), "name": "workspace"}]
    response = server.handle(request("initialize", 1, params))
    assert response is not None
    server.handle(notify("initialized", {}))


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


def test_namespace_import_parser_tracks_alias_and_qualified_member_span() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import * as api from ./provider.nova;\n"
        "fn main() { api::target(); }\n"
    )

    tree = server.nova_adapter.parse(text)

    assert len(tree.imports) == 1
    imported = tree.imports[0]
    assert imported.namespace == "api"
    assert imported.namespace_span is not None
    assert text[imported.namespace_span.start : imported.namespace_span.end] == "api"
    assert imported.path == "./provider.nova"
    assert tree.calls == (
        (
            "api::target",
            next(
                span
                for name, span in tree.calls
                if name == "api::target"
            ),
        ),
    )
    call_span = tree.calls[0][1]
    assert text[call_span.start : call_span.end] == "target"
    assert all(item.name not in {"api", "target"} for item in tree.unresolved_names)


def test_namespace_call_respects_exports_and_private_visibility() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = (
        "fn visible() {}\n"
        "private fn hidden() {}\n"
        "export { visible };\n"
    )
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn caller() { api::visible(); api::hidden(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)

    codes = diagnostic_codes(server, caller_uri)
    assert codes.count("nova.unresolved-function") == 1

    definition = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "visible", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == provider_uri

    references = server.handle(
        request(
            "textDocument/references",
            11,
            {
                "textDocument": {"uri": provider_uri},
                "position": position(provider, "visible", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert caller_uri in {item["uri"] for item in references["result"]}


def test_namespace_argument_count_uses_shared_quick_fix() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(
        server,
        provider_uri,
        "fn target(left: Int, right: Int) {}\n",
    )
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn caller() { api::target(1); }\n"
    )
    open_nova(server, caller_uri, caller)

    assert "nova.argument-count" in diagnostic_codes(server, caller_uri)
    target = position(caller, "target", delta=1)
    action = server.handle(
        request(
            "textDocument/codeAction",
            12,
            {
                "textDocument": {"uri": caller_uri},
                "range": {"start": target, "end": target},
                "context": {"diagnostics": []},
            },
        )
    )
    assert action is not None
    assert any(
        item["title"] == "Adjust 'api::target' to 2 argument(s)"
        for item in action["result"]
    )


def test_duplicate_and_reserved_namespace_bindings_fail_closed() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    left_uri = "file:///workspace/left.nova"
    right_uri = "file:///workspace/right.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, left_uri, "fn left() {}\n")
    open_nova(server, right_uri, "fn right() {}\n")
    caller = (
        "import * as api from ./left.nova;\n"
        "import * as api from ./right.nova;\n"
        "import * as UInt from ./left.nova;\n"
        "fn caller() { api::left(); UInt::left(); }\n"
    )
    open_nova(server, caller_uri, caller)

    codes = diagnostic_codes(server, caller_uri)
    assert "nova.duplicate-import-namespace" in codes
    assert "nova.reserved-import-namespace" in codes
    assert codes.count("nova.unresolved-function") == 2


def test_namespace_import_does_not_reexport_implicitly() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, provider_uri, "fn target() {}\n")
    open_nova(
        server,
        middle_uri,
        "import * as api from ./provider.nova;\nfn middle() { api::target(); }\n",
    )
    root = "import ./middle.nova;\nfn root() { api::target(); }\n"
    open_nova(server, root_uri, root)

    assert "nova.unresolved-function" not in diagnostic_codes(server, middle_uri)
    assert "nova.unresolved-function" in diagnostic_codes(server, root_uri)


def test_closed_namespace_call_uses_detached_workspace_resolution(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn target(left: Int, right: Int) {}\n",
        encoding="utf-8",
    )
    caller.write_text(
        (
            "import * as api from ./provider.nova;\n"
            "fn caller() { api::target(1); }\n"
        ),
        encoding="utf-8",
    )
    server = NovaProductLanguageServer()
    initialize(server, root=tmp_path)
    caller_uri = caller.as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            20,
            {"textDocument": {"uri": caller_uri}},
        )
    )

    assert response is not None
    codes = [item["code"] for item in response["result"]["items"]]
    assert "nova.argument-count" in codes
    assert "nova.unresolved-function" not in codes
    assert server.documents.get(caller_uri) is None
    assert server.diagnostics.get(caller_uri) is None


def test_namespace_member_prepare_and_rename_preserve_prefix() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = "fn target() {}\n"
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn caller() { api::target(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)
    member = position(caller, "target", delta=1)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            30,
            {
                "textDocument": {"uri": caller_uri},
                "position": member,
            },
        )
    )
    assert prepared is not None
    assert prepared["result"]["placeholder"] == "target"

    renamed = server.handle(
        request(
            "textDocument/rename",
            31,
            {
                "textDocument": {"uri": caller_uri},
                "position": member,
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    changes = renamed["result"]["changes"]
    assert {edit["newText"] for edit in changes[provider_uri]} == {"renamed"}
    caller_edits = changes[caller_uri]
    assert [edit["newText"] for edit in caller_edits] == ["renamed"]
    edit_range = caller_edits[0]["range"]
    assert edit_range["start"]["character"] == caller.index("target")
    assert edit_range["end"]["character"] == caller.index("target") + len("target")


def test_file_rename_rewrites_namespace_import_path(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    renamed = tmp_path / "renamed.nova"
    caller_uri = (tmp_path / "caller.nova").as_uri()
    source = (
        "import * as api from ./provider.nova;\n"
        "fn caller() { api::target(); }\n"
    )
    server = NovaProductLanguageServer()
    initialize(
        server,
        root=tmp_path,
        will_rename=True,
        document_changes=True,
    )
    open_nova(server, caller_uri, source, version=7)

    response = server.handle(
        request(
            "workspace/willRenameFiles",
            40,
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
    path_start = source.index("./provider.nova")
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": caller_uri, "version": 7},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": path_start},
                        "end": {
                            "line": 0,
                            "character": path_start + len("./provider.nova"),
                        },
                    },
                    "newText": "./renamed.nova",
                }
            ],
        }
    ]
