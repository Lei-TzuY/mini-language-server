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
    document_changes: bool = True,
) -> None:
    workspace: dict[str, Any] = {
        "workspaceEdit": {"documentChanges": document_changes},
    }
    params: dict[str, Any] = {
        "capabilities": {"workspace": workspace},
    }
    if root is not None:
        workspace["workspaceFolders"] = True
        params["workspaceFolders"] = [{"uri": root.as_uri(), "name": "workspace"}]

    response = server.handle(request("initialize", 1, params))
    assert response is not None
    if root is not None:
        server.handle(notify("initialized", {}))


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int,
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


def position(text: str, marker: str, *, occurrence: int = 0, delta: int = 0) -> dict[str, int]:
    start = 0
    offset = -1
    for _ in range(occurrence + 1):
        offset = text.index(marker, start)
        start = offset + len(marker)
    offset += delta
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": offset - line_start}


def rename(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    marker: str,
    occurrence: int = 0,
    new_name: str = "renamed",
    request_id: int = 10,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/rename",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": position(
                    text,
                    marker,
                    occurrence=occurrence,
                    delta=1,
                ),
                "newName": new_name,
            },
        )
    )
    assert response is not None
    return response


def edits_by_uri(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["textDocument"]["uri"]: item
        for item in result["documentChanges"]
    }


def test_rename_updates_open_selective_import_and_export_binding_chain() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = "fn target() {}\nexport { target };\n"
    middle = (
        "import { target } from ./provider.nova;\n"
        "export { target };\n"
    )
    caller = (
        "import { target } from ./middle.nova;\n"
        "fn caller() { target(); }\n"
    )
    open_nova(server, provider_uri, provider, version=2)
    open_nova(server, middle_uri, middle, version=3)
    open_nova(server, caller_uri, caller, version=4)

    response = rename(
        server,
        caller_uri,
        caller,
        marker="target();",
    )

    assert "error" not in response
    grouped = edits_by_uri(response["result"])
    assert set(grouped) == {provider_uri, middle_uri, caller_uri}
    assert grouped[provider_uri]["textDocument"]["version"] == 2
    assert grouped[middle_uri]["textDocument"]["version"] == 3
    assert grouped[caller_uri]["textDocument"]["version"] == 4
    assert [edit["newText"] for edit in grouped[provider_uri]["edits"]] == [
        "renamed",
        "renamed",
    ]
    assert [edit["newText"] for edit in grouped[middle_uri]["edits"]] == [
        "renamed",
        "renamed",
    ]
    assert [edit["newText"] for edit in grouped[caller_uri]["edits"]] == [
        "renamed",
        "renamed",
    ]


def test_rename_does_not_rewrite_invalid_private_export_entry() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    provider = "private fn target() {}\nexport { target };\n"
    open_nova(server, provider_uri, provider, version=5)

    response = rename(
        server,
        provider_uri,
        provider,
        marker="target",
    )

    assert "error" not in response
    grouped = edits_by_uri(response["result"])
    assert set(grouped) == {provider_uri}
    assert grouped[provider_uri]["textDocument"]["version"] == 5
    assert [edit["newText"] for edit in grouped[provider_uri]["edits"]] == [
        "renamed"
    ]


def test_rename_updates_closed_module_bindings_with_null_versions(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    middle = tmp_path / "middle.nova"
    caller = tmp_path / "caller.nova"
    provider_text = "fn target() {}\nexport { target };\n"
    middle_text = (
        "import { target } from ./provider.nova;\n"
        "export { target };\n"
    )
    caller_text = (
        "import { target } from ./middle.nova;\n"
        "fn caller() { target(); }\n"
    )
    provider.write_text(provider_text, encoding="utf-8")
    middle.write_text(middle_text, encoding="utf-8")
    caller.write_text(caller_text, encoding="utf-8")

    server = NovaProductLanguageServer()
    initialize(server, root=tmp_path)
    caller_uri = caller.as_uri()
    open_nova(server, caller_uri, caller_text, version=7)

    response = rename(
        server,
        caller_uri,
        caller_text,
        marker="target();",
    )

    assert "error" not in response
    grouped = edits_by_uri(response["result"])
    assert set(grouped) == {provider.as_uri(), middle.as_uri(), caller_uri}
    assert grouped[provider.as_uri()]["textDocument"]["version"] is None
    assert grouped[middle.as_uri()]["textDocument"]["version"] is None
    assert grouped[caller_uri]["textDocument"]["version"] == 7
    assert [edit["newText"] for edit in grouped[provider.as_uri()]["edits"]] == [
        "renamed",
        "renamed",
    ]
    assert [edit["newText"] for edit in grouped[middle.as_uri()]["edits"]] == [
        "renamed",
        "renamed",
    ]
    assert [edit["newText"] for edit in grouped[caller_uri]["edits"]] == [
        "renamed",
        "renamed",
    ]
