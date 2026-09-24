from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    document_changes: bool = False,
) -> None:
    workspace_edit: dict[str, Any] = {}
    if document_changes:
        workspace_edit["documentChanges"] = True
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "workspaceEdit": workspace_edit,
                    }
                }
            },
        )
    )
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


def prepare(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    text: str,
    marker: str,
    *,
    delta: int = 0,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/prepareRename",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": position(text, marker, delta=delta),
            },
        )
    )
    assert response is not None
    return response


def rename(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    text: str,
    marker: str,
    new_name: str,
    *,
    delta: int = 0,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/rename",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": position(text, marker, delta=delta),
                "newName": new_name,
            },
        )
    )
    assert response is not None
    return response


def test_parser_retains_exact_namespace_qualifier_references() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import * as api from ./provider.nova;\n"
        "fn main() { api::target(); api::missing(); }\n"
    )

    tree = server.nova_adapter.parse(text)

    assert [(name, text[span.start : span.end]) for name, span in tree.namespace_references] == [
        ("api", "api"),
        ("api", "api"),
    ]


def test_prepare_namespace_rename_from_declaration_and_qualifier() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn target() {}\n")
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() { api::target(); }\n"
    )
    open_nova(server, caller_uri, caller)

    declared = prepare(server, caller_uri, 10, caller, "api")
    qualified = prepare(server, caller_uri, 11, caller, "api::target", delta=1)

    assert declared["result"]["placeholder"] == "api"
    assert qualified["result"]["placeholder"] == "api"
    assert declared["result"]["range"] == {
        "start": {"line": 0, "character": len("import * as ")},
        "end": {"line": 0, "character": len("import * as api")},
    }
    assert qualified["result"]["range"] == {
        "start": {"line": 1, "character": len("fn main() { ")},
        "end": {"line": 1, "character": len("fn main() { api")},
    }


def test_namespace_rename_edits_declaration_and_all_importer_qualifiers() -> None:
    server = NovaProductLanguageServer()
    initialize(server, document_changes=True)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn target() {}\n", version=4)
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() { api::target(); api::missing(); }\n"
    )
    open_nova(server, caller_uri, caller, version=7)

    response = rename(
        server,
        caller_uri,
        12,
        caller,
        "api::target",
        "service",
        delta=1,
    )

    document_changes = response["result"]["documentChanges"]
    assert len(document_changes) == 1
    assert document_changes[0]["textDocument"] == {
        "uri": caller_uri,
        "version": 7,
    }
    edits = document_changes[0]["edits"]
    assert [edit["newText"] for edit in edits] == [
        "service",
        "service",
        "service",
    ]
    assert [edit["range"]["start"] for edit in edits] == [
        {"line": 0, "character": len("import * as ")},
        {"line": 1, "character": len("fn main() { ")},
        {
            "line": 1,
            "character": len("fn main() { api::target(); "),
        },
    ]
    assert provider_uri not in {
        change["textDocument"]["uri"]
        for change in document_changes
        if "textDocument" in change
    }


def test_namespace_rename_rejects_invalid_reserved_and_conflicting_names() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    other_uri = "file:///workspace/other.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn target() {}\n")
    open_nova(server, other_uri, "fn other() {}\n")
    caller = (
        "import * as api from ./provider.nova;\n"
        "import * as util from ./other.nova;\n"
        "fn main() { api::target(); util::other(); }\n"
    )
    open_nova(server, caller_uri, caller)

    invalid = rename(server, caller_uri, 20, caller, "api::target", "bad-name", delta=1)
    reserved = rename(server, caller_uri, 21, caller, "api::target", "UInt", delta=1)
    conflict = rename(server, caller_uri, 22, caller, "api::target", "util", delta=1)

    assert invalid["error"] == {"code": -32602, "message": "Invalid params"}
    assert reserved["error"] == {
        "code": -32803,
        "message": "Rename would conflict with reserved namespace 'UInt'",
    }
    assert conflict["error"] == {
        "code": -32803,
        "message": "Rename would conflict with import namespace 'util'",
    }


def test_duplicate_namespace_binding_does_not_offer_namespace_rename() -> None:
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
        "fn main() { api::left(); }\n"
    )
    open_nova(server, caller_uri, caller)

    prepared = prepare(server, caller_uri, 30, caller, "api::left", delta=1)

    assert prepared["result"] is None


def test_member_rename_still_targets_canonical_function_not_namespace() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = "fn target() {}\n"
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() { api::target(); }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)

    response = rename(
        server,
        caller_uri,
        40,
        caller,
        "target",
        "renamed",
        delta=1,
    )

    changes = response["result"]["changes"]
    assert {edit["newText"] for edit in changes[provider_uri]} == {"renamed"}
    assert [edit["newText"] for edit in changes[caller_uri]] == ["renamed"]
    caller_start = changes[caller_uri][0]["range"]["start"]
    assert caller_start == {
        "line": 1,
        "character": len("fn main() { api::"),
    }
