from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic_tokens import TOKEN_TYPES


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
                "documentHighlight": {},
                "semanticTokens": {
                    "requests": {"full": True},
                    "tokenModifiers": ["declaration"],
                },
                "completion": {},
                "diagnostic": {},
                "rename": {"prepareSupport": True},
            },
            "workspace": {"workspaceFolders": True},
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


def decode_tokens(data: list[int]) -> list[tuple[int, int, int, int, int]]:
    result: list[tuple[int, int, int, int, int]] = []
    line = 0
    character = 0
    for index in range(0, len(data), 5):
        delta_line, delta_start, length, token_type, modifiers = data[index : index + 5]
        line += delta_line
        character = character + delta_start if delta_line == 0 else delta_start
        result.append((line, character, length, token_type, modifiers))
    return result


def selective_namespace_fixture() -> tuple[
    NovaProductLanguageServer,
    str,
    str,
    str,
    str,
]:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    provider = (
        "fn target(value: Int) -> Int { return value; }\n"
        "private fn hidden() {}\n"
        "export { target };\n"
    )
    middle = (
        "import * as api from ./provider.nova;\n"
        "export { api };\n"
    )
    root = (
        "import { api as facade } from ./middle.nova;\n"
        "fn root() { facade::target(1); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, middle_uri, middle)
    open_nova(server, root_uri, root)
    return server, provider_uri, middle_uri, root_uri, root


def test_parser_keeps_selective_binding_as_qualified_namespace_candidate() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import { api as facade } from ./middle.nova;\n"
        "fn root() { facade::target(); }\n"
    )

    tree = server.nova_adapter.parse(text)

    assert tree.calls == (
        ("facade::target", next(span for name, span in tree.calls if name == "facade::target")),
    )
    assert [(name, text[span.start : span.end]) for name, span in tree.namespace_references] == [
        ("facade", "facade")
    ]


def test_selective_namespace_alias_resolves_qualified_member() -> None:
    server, provider_uri, _, root_uri, root = selective_namespace_fixture()

    assert "nova.unresolved-import-name" not in diagnostic_codes(server, root_uri)
    assert "nova.unresolved-function" not in diagnostic_codes(server, root_uri)

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
    result = definition["result"]
    assert result[0]["targetUri"] == provider_uri


def test_selective_namespace_alias_has_local_identity_surfaces() -> None:
    server, _, _, root_uri, root = selective_namespace_fixture()
    alias = position(root, "facade }", delta=1)
    qualifier = position(root, "facade::target", delta=1)

    defined = server.handle(
        request(
            "textDocument/definition",
            20,
            {"textDocument": {"uri": root_uri}, "position": qualifier},
        )
    )
    assert defined is not None
    assert defined["result"][0]["targetUri"] == root_uri
    assert defined["result"][0]["targetRange"]["start"] == position(root, "facade }")

    refs = server.handle(
        request(
            "textDocument/references",
            21,
            {
                "textDocument": {"uri": root_uri},
                "position": alias,
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert refs is not None
    assert [item["range"]["start"] for item in refs["result"]] == [
        position(root, "facade }"),
        position(root, "facade::target"),
    ]

    highlights = server.handle(
        request(
            "textDocument/documentHighlight",
            22,
            {"textDocument": {"uri": root_uri}, "position": qualifier},
        )
    )
    assert highlights is not None
    assert [(item["range"]["start"], item["kind"]) for item in highlights["result"]] == [
        (position(root, "facade }"), 3),
        (position(root, "facade::target"), 2),
    ]

    tokens = server.handle(
        request(
            "textDocument/semanticTokens/full",
            23,
            {"textDocument": {"uri": root_uri}},
        )
    )
    assert tokens is not None
    namespace_type = TOKEN_TYPES.index("namespace")
    namespace_tokens = [
        item
        for item in decode_tokens(tokens["result"]["data"])
        if item[3] == namespace_type
    ]
    assert [(line, character, length) for line, character, length, _, _ in namespace_tokens] == [
        (0, len("import { api as "), len("facade")),
        (1, len("fn root() { "), len("facade")),
    ]
    assert [item[4] for item in namespace_tokens] == [1, 0]


def test_selective_namespace_member_completion_projects_only_members() -> None:
    server, _, _, root_uri, root = selective_namespace_fixture()
    changed = (
        "import { api as facade } from ./middle.nova;\n"
        "fn root() { facade::ta; }\n"
    )
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": root_uri, "version": 2},
                "contentChanges": [{"text": changed}],
            },
        )
    )

    response = server.handle(
        request(
            "textDocument/completion",
            30,
            {
                "textDocument": {"uri": root_uri},
                "position": position(changed, "facade::ta", delta=len("facade::ta")),
            },
        )
    )
    assert response is not None
    result = response["result"]
    items = result if isinstance(result, list) else result["items"]
    assert [item["label"] for item in items] == ["target"]

    plain = (
        "import { api as facade } from ./middle.nova;\n"
        "fn root() { ta }\n"
    )
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": root_uri, "version": 3},
                "contentChanges": [{"text": plain}],
            },
        )
    )
    response = server.handle(
        request(
            "textDocument/completion",
            31,
            {
                "textDocument": {"uri": root_uri},
                "position": position(plain, " ta", delta=len(" ta")),
            },
        )
    )
    assert response is not None
    result = response["result"]
    items = result if isinstance(result, list) else result["items"]
    labels = [item["label"] for item in items]
    assert "target" not in labels
    assert "facade::target" not in labels


def test_selective_namespace_alias_rename_is_importer_local() -> None:
    server, _, middle_uri, root_uri, root = selective_namespace_fixture()
    qualifier = position(root, "facade::target", delta=1)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            40,
            {"textDocument": {"uri": root_uri}, "position": qualifier},
        )
    )
    assert prepared is not None
    assert prepared["result"]["placeholder"] == "facade"

    renamed = server.handle(
        request(
            "textDocument/rename",
            41,
            {
                "textDocument": {"uri": root_uri},
                "position": qualifier,
                "newName": "surface",
            },
        )
    )
    assert renamed is not None
    changes = renamed["result"]["changes"]
    assert list(changes) == [root_uri]
    assert [item["newText"] for item in changes[root_uri]] == ["surface", "surface"]
    assert middle_uri not in changes


def test_unaliased_selective_namespace_rename_stays_fail_closed() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, provider_uri, "fn target() {}\nexport { target };\n")
    open_nova(
        server,
        middle_uri,
        "import * as api from ./provider.nova;\nexport { api };\n",
    )
    root = "import { api } from ./middle.nova;\nfn root() { api::target(); }\n"
    open_nova(server, root_uri, root)

    prepared = server.handle(
        request(
            "textDocument/prepareRename",
            50,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "api::target", delta=1),
            },
        )
    )
    assert prepared is not None
    assert prepared["result"] is None


def test_selective_namespace_function_collision_is_ambiguous() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    root_uri = "file:///workspace/root.nova"
    open_nova(server, provider_uri, "fn target() {}\nexport { target };\n")
    open_nova(
        server,
        middle_uri,
        (
            "import * as api from ./provider.nova;\n"
            "fn api() {}\n"
            "export { api };\n"
        ),
    )
    root = "import { api as facade } from ./middle.nova;\nfn root() { facade::target(); }\n"
    open_nova(server, root_uri, root)

    codes = diagnostic_codes(server, root_uri)
    assert "nova.ambiguous-import-name" in codes
    assert "nova.unresolved-function" in codes


def test_closed_selective_namespace_uses_detached_workspace_graph(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    middle = tmp_path / "middle.nova"
    root = tmp_path / "root.nova"
    provider.write_text("fn target() {}\nexport { target };\n", encoding="utf-8")
    middle.write_text(
        "import * as api from ./provider.nova;\nexport { api };\n",
        encoding="utf-8",
    )
    root.write_text(
        "import { api as facade } from ./middle.nova;\n"
        "fn root() { facade::target(); }\n",
        encoding="utf-8",
    )
    server = NovaProductLanguageServer()
    initialize(server, root=tmp_path)
    root_uri = root.as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            60,
            {"textDocument": {"uri": root_uri}},
        )
    )
    assert response is not None
    codes = [item["code"] for item in response["result"]["items"]]
    assert "nova.unresolved-import-name" not in codes
    assert "nova.unresolved-function" not in codes
    assert server.documents.get(root_uri) is None
    assert server.diagnostics.get(root_uri) is None
