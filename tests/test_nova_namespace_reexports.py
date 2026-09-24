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
) -> None:
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": {
                "definition": {"linkSupport": True},
                "diagnostic": {},
            },
            "workspace": {"workspaceFolders": True},
        }
    }
    if root is not None:
        params["workspaceFolders"] = [{"uri": root.as_uri(), "name": "workspace"}]
    response = server.handle(request("initialize", 1, params))
    assert response is not None
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
    return [item.code for item in snapshot.diagnostics if item.code is not None]


def test_namespace_reexport_propagates_exact_provider_members() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    provider = (
        "fn target() {}\n"
        "private fn hidden() {}\n"
        "export { target };\n"
    )
    middle = (
        "import * as api from ./provider.nova;\n"
        "export { api };\n"
        "fn middle() { api::target(); }\n"
    )
    root = (
        "import * as facade from ./middle.nova;\n"
        "fn root() { facade::target(); facade::hidden(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, middle_uri, middle)
    open_nova(server, root_uri, root)

    assert "nova.unresolved-export" not in diagnostic_codes(server, middle_uri)
    assert diagnostic_codes(server, root_uri).count("nova.unresolved-function") == 1

    definition = server.handle(
        request(
            "textDocument/definition",
            10,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "target", delta=1),
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
                "position": position(provider, "target", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert {item["uri"] for item in references["result"]} == {
        provider_uri,
        middle_uri,
        root_uri,
    }


def test_namespace_export_selector_reuses_importer_namespace_identity() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    open_nova(server, provider_uri, "fn target() {}\n")
    middle = (
        "import * as api from ./provider.nova;\n"
        "export { api };\n"
        "fn middle() { api::target(); }\n"
    )
    open_nova(server, middle_uri, middle)

    defined = server.handle(
        request(
            "textDocument/definition",
            20,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "api };", delta=1),
            },
        )
    )
    assert defined is not None
    assert defined["result"][0]["targetUri"] == middle_uri
    assert defined["result"][0]["targetSelectionRange"]["start"] == {
        "line": 0,
        "character": len("import * as "),
    }

    references = server.handle(
        request(
            "textDocument/references",
            21,
            {
                "textDocument": {"uri": middle_uri},
                "position": position(middle, "api };", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["range"]["start"]["line"] for item in references["result"]] == [0, 1, 2]


def test_namespace_reexport_member_rename_tracks_canonical_identity() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    provider = "fn target() {}\nexport { target };\n"
    middle = (
        "import * as api from ./provider.nova;\n"
        "export { api };\n"
        "fn middle() { api::target(); }\n"
    )
    root = (
        "import * as facade from ./middle.nova;\n"
        "fn root() { facade::target(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, middle_uri, middle)
    open_nova(server, root_uri, root)

    renamed = server.handle(
        request(
            "textDocument/rename",
            30,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "target", delta=1),
                "newName": "renamed",
            },
        )
    )
    assert renamed is not None
    changes = renamed["result"]["changes"]
    assert {provider_uri, middle_uri, root_uri} <= set(changes)
    assert {edit["newText"] for edit in changes[provider_uri]} == {"renamed"}
    assert [edit["newText"] for edit in changes[middle_uri]] == ["renamed"]
    assert [edit["newText"] for edit in changes[root_uri]] == ["renamed"]


def test_namespace_reexport_collision_fails_closed() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, provider_uri, "fn target() {}\n")
    middle = (
        "import * as api from ./provider.nova;\n"
        "fn api() {}\n"
        "export { api };\n"
    )
    root = (
        "import * as facade from ./middle.nova;\n"
        "fn root() { facade::target(); }\n"
    )
    open_nova(server, middle_uri, middle)
    open_nova(server, root_uri, root)

    assert "nova.ambiguous-export" in diagnostic_codes(server, middle_uri)
    assert "nova.unresolved-function" in diagnostic_codes(server, root_uri)


def test_closed_namespace_reexport_uses_detached_graph(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    middle = tmp_path / "middle.nova"
    root = tmp_path / "root.nova"
    provider.write_text(
        "fn target(left: Int, right: Int) {}\nexport { target };\n",
        encoding="utf-8",
    )
    middle.write_text(
        (
            "import * as api from ./provider.nova;\n"
            "export { api };\n"
        ),
        encoding="utf-8",
    )
    root.write_text(
        (
            "import * as facade from ./middle.nova;\n"
            "fn root() { facade::target(1); }\n"
        ),
        encoding="utf-8",
    )
    server = NovaProductLanguageServer()
    initialize(server, root=tmp_path)
    root_uri = root.as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            40,
            {"textDocument": {"uri": root_uri}},
        )
    )

    assert response is not None
    codes = [item["code"] for item in response["result"]["items"]]
    assert "nova.argument-count" in codes
    assert "nova.unresolved-function" not in codes
    assert server.documents.get(root_uri) is None
    assert server.diagnostics.get(root_uri) is None


def test_selective_namespace_reexport_chains_exact_object_identity() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    first_uri = "file:///workspace/first.nova"
    second_uri = "file:///workspace/second.nova"
    root_uri = "file:///workspace/root.nova"
    provider = "fn target() {}\nexport { target };\n"
    first = "import * as api from ./provider.nova;\nexport { api };\n"
    second = (
        "import { api as facade } from ./first.nova;\n"
        "export { facade };\n"
        "fn second() { facade::target(); }\n"
    )
    root = (
        "import { facade as surface } from ./second.nova;\n"
        "fn root() { surface::target(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, first_uri, first)
    open_nova(server, second_uri, second)
    open_nova(server, root_uri, root)

    assert "nova.unresolved-import-name" not in diagnostic_codes(server, second_uri)
    assert "nova.unresolved-export" not in diagnostic_codes(server, second_uri)
    assert "nova.unresolved-function" not in diagnostic_codes(server, second_uri)
    assert "nova.unresolved-import-name" not in diagnostic_codes(server, root_uri)
    assert "nova.unresolved-function" not in diagnostic_codes(server, root_uri)

    definition = server.handle(
        request(
            "textDocument/definition",
            50,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "target", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == provider_uri

    references = server.handle(
        request(
            "textDocument/references",
            51,
            {
                "textDocument": {"uri": provider_uri},
                "position": position(provider, "target", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert {item["uri"] for item in references["result"]} == {
        provider_uri,
        second_uri,
        root_uri,
    }


def test_selective_namespace_reexport_selector_shares_local_identity() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    first_uri = "file:///workspace/first.nova"
    second_uri = "file:///workspace/second.nova"
    open_nova(server, provider_uri, "fn target() {}\nexport { target };\n")
    open_nova(
        server,
        first_uri,
        "import * as api from ./provider.nova;\nexport { api };\n",
    )
    second = (
        "import { api as facade } from ./first.nova;\n"
        "export { facade };\n"
        "fn second() { facade::target(); }\n"
    )
    open_nova(server, second_uri, second)

    defined = server.handle(
        request(
            "textDocument/definition",
            60,
            {
                "textDocument": {"uri": second_uri},
                "position": position(second, "facade };", delta=1),
            },
        )
    )
    assert defined is not None
    assert defined["result"][0]["targetUri"] == second_uri
    assert defined["result"][0]["targetSelectionRange"]["start"] == position(
        second,
        "facade } from",
    )

    references = server.handle(
        request(
            "textDocument/references",
            61,
            {
                "textDocument": {"uri": second_uri},
                "position": position(second, "facade };", delta=1),
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["range"]["start"]["line"] for item in references["result"]] == [0, 1, 2]


def test_selective_namespace_reexport_function_collision_fails_closed() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    first_uri = "file:///workspace/first.nova"
    second_uri = "file:///workspace/second.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, provider_uri, "fn target() {}\nexport { target };\n")
    open_nova(
        server,
        first_uri,
        "import * as api from ./provider.nova;\nexport { api };\n",
    )
    second = (
        "import { api as facade } from ./first.nova;\n"
        "fn facade() {}\n"
        "export { facade };\n"
    )
    root = (
        "import { facade as surface } from ./second.nova;\n"
        "fn root() { surface::target(); }\n"
    )
    open_nova(server, second_uri, second)
    open_nova(server, root_uri, root)

    assert "nova.ambiguous-export" in diagnostic_codes(server, second_uri)
    assert "nova.unresolved-function" in diagnostic_codes(server, root_uri)


def test_closed_selective_namespace_reexport_uses_detached_graph(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    root = tmp_path / "root.nova"
    provider.write_text(
        "fn target(left: Int, right: Int) {}\nexport { target };\n",
        encoding="utf-8",
    )
    first.write_text(
        "import * as api from ./provider.nova;\nexport { api };\n",
        encoding="utf-8",
    )
    second.write_text(
        (
            "import { api as facade } from ./first.nova;\n"
            "export { facade };\n"
        ),
        encoding="utf-8",
    )
    root.write_text(
        (
            "import { facade as surface } from ./second.nova;\n"
            "fn root() { surface::target(1); }\n"
        ),
        encoding="utf-8",
    )
    server = NovaProductLanguageServer()
    initialize(server, root=tmp_path)
    root_uri = root.as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            70,
            {"textDocument": {"uri": root_uri}},
        )
    )

    assert response is not None
    codes = [item["code"] for item in response["result"]["items"]]
    assert "nova.unresolved-import-name" not in codes
    assert "nova.argument-count" in codes
    assert "nova.unresolved-function" not in codes
    assert server.documents.get(root_uri) is None
    assert server.diagnostics.get(root_uri) is None


def test_exported_selective_namespace_rename_stays_fail_closed() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    first_uri = "file:///workspace/first.nova"
    second_uri = "file:///workspace/second.nova"
    open_nova(server, provider_uri, "fn target() {}\nexport { target };\n")
    open_nova(
        server,
        first_uri,
        "import * as api from ./provider.nova;\nexport { api };\n",
    )
    second = (
        "import { api as facade } from ./first.nova;\n"
        "export { facade };\n"
        "fn second() { facade::target(); }\n"
    )
    open_nova(server, second_uri, second)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            80,
            {
                "textDocument": {"uri": second_uri},
                "position": position(second, "facade::target", delta=1),
            },
        )
    )

    assert prepared is not None
    assert prepared["result"] is None
