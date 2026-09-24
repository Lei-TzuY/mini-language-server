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
    snippets: bool = False,
    insert_replace: bool = False,
    item_defaults: bool = False,
    resolve_detail: bool = False,
    inline: bool = False,
) -> None:
    completion_item: dict[str, Any] = {
        "snippetSupport": snippets,
        "insertReplaceSupport": insert_replace,
    }
    if resolve_detail:
        completion_item["resolveSupport"] = {"properties": ["detail"]}
    completion: dict[str, Any] = {"completionItem": completion_item}
    if item_defaults:
        completion["completionList"] = {"itemDefaults": ["editRange"]}
    text_document: dict[str, Any] = {"completion": completion}
    if inline:
        text_document["inlineCompletion"] = {}
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": text_document,
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


def position_after(text: str, marker: str) -> dict[str, int]:
    offset = text.index(marker) + len(marker)
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": offset - line_start}


def completion(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    text: str,
    marker: str,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": position_after(text, marker),
            },
        )
    )
    assert response is not None
    return response


def completion_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response["result"]
    if isinstance(result, list):
        return result
    assert isinstance(result, dict)
    items = result.get("items")
    assert isinstance(items, list)
    return items


def test_namespace_completion_projects_only_exported_members() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    provider = (
        "fn target(value: Int) -> Int { return value; }\n"
        "fn other() -> Unit { return (); }\n"
        "private fn hidden() -> Unit { return (); }\n"
        "export { target, other };\n"
    )
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() -> Unit { api::ta; }\n"
    )
    open_nova(server, provider_uri, provider)
    open_nova(server, caller_uri, caller)

    response = completion(server, caller_uri, 2, caller, "api::ta")

    assert [item["label"] for item in completion_items(response)] == ["target"]
    item = completion_items(response)[0]
    assert item["detail"] == "fn target(value: Int) -> Int"
    assert item["insertText"] == "target(${1:value})$0"
    assert item["insertTextFormat"] == 2


def test_empty_namespace_prefix_lists_outward_view_without_plain_leakage() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(
        server,
        provider_uri,
        (
            "fn alpha() -> Unit { return (); }\n"
            "fn beta() -> Unit { return (); }\n"
            "private fn hidden() -> Unit { return (); }\n"
            "export { alpha, beta };\n"
        ),
    )
    qualified = (
        "import * as api from ./provider.nova;\n"
        "fn main() -> Unit { api:: }\n"
    )
    open_nova(server, caller_uri, qualified)

    qualified_response = completion(server, caller_uri, 2, qualified, "api::")
    assert [item["label"] for item in completion_items(qualified_response)] == [
        "alpha",
        "beta",
    ]

    plain = (
        "import * as api from ./provider.nova;\n"
        "fn main() -> Unit { al }\n"
    )
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": caller_uri, "version": 2},
                "contentChanges": [{"text": plain}],
            },
        )
    )
    plain_response = completion(server, caller_uri, 3, plain, " al")
    labels = [item["label"] for item in completion_items(plain_response)]
    assert "api::alpha" not in labels
    assert "alpha" not in labels


def test_namespace_completion_preserves_shared_edit_range_and_lazy_resolve() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        snippets=True,
        insert_replace=True,
        item_defaults=True,
        resolve_detail=True,
    )
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(
        server,
        provider_uri,
        "fn target(value: Int) -> Int { return value; }\n",
    )
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() -> Unit { api::tafoo; }\n"
    )
    open_nova(server, caller_uri, caller)

    response = completion(server, caller_uri, 2, caller, "api::ta")
    result = response["result"]
    assert isinstance(result, dict)
    member_start = caller.index("tafoo")
    line_start = caller.rfind("\n", 0, member_start) + 1
    start_character = member_start - line_start
    assert result["itemDefaults"]["editRange"] == {
        "insert": {
            "start": {"line": 1, "character": start_character},
            "end": {"line": 1, "character": start_character + 2},
        },
        "replace": {
            "start": {"line": 1, "character": start_character},
            "end": {"line": 1, "character": start_character + len("tafoo")},
        },
    }
    item = completion_items(response)[0]
    assert item["label"] == "target"
    assert "detail" not in item
    assert item["textEditText"] == "target(${1:value})$0"
    assert isinstance(item.get("data"), dict)

    resolved = server.handle(request("completionItem/resolve", 3, item))
    assert resolved is not None
    assert resolved["result"]["detail"] == "fn target(value: Int) -> Int"
    assert resolved["result"]["label"] == "target"


def test_namespace_inline_completion_reuses_member_projection() -> None:
    server = NovaProductLanguageServer()
    initialize(server, inline=True)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn target() -> Unit { return (); }\n")
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() -> Unit { api::tafoo; }\n"
    )
    open_nova(server, caller_uri, caller)

    response = server.handle(
        request(
            "textDocument/inlineCompletion",
            2,
            {
                "textDocument": {"uri": caller_uri},
                "position": position_after(caller, "api::ta"),
                "context": {"triggerKind": 1},
            },
        )
    )

    assert response is not None
    member_start = caller.index("tafoo")
    line_start = caller.rfind("\n", 0, member_start) + 1
    start_character = member_start - line_start
    assert response["result"] == [
        {
            "insertText": "target",
            "range": {
                "start": {"line": 1, "character": start_character},
                "end": {
                    "line": 1,
                    "character": start_character + len("tafoo"),
                },
            },
        }
    ]


def test_namespace_completion_uses_closed_provider_snapshot(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    provider.write_text(
        "fn detached(value: Int) -> Int { return value; }\n",
        encoding="utf-8",
    )
    server = NovaProductLanguageServer()
    initialize(server, root=tmp_path)
    caller_uri = (tmp_path / "caller.nova").as_uri()
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main() -> Unit { api::de; }\n"
    )
    open_nova(server, caller_uri, caller)

    response = completion(server, caller_uri, 2, caller, "api::de")

    assert [item["label"] for item in completion_items(response)] == ["detached"]
    assert completion_items(response)[0]["detail"] == (
        "fn detached(value: Int) -> Int"
    )
    assert server.documents.get(provider.as_uri()) is None


def test_unknown_namespace_fails_closed_but_uint_intrinsic_completion_survives() -> None:
    server = NovaProductLanguageServer()
    initialize(server, snippets=True)
    uri = "file:///workspace/main.nova"
    unknown = "fn main() -> Unit { ghost::fr; }\n"
    open_nova(server, uri, unknown)

    unknown_response = completion(server, uri, 2, unknown, "ghost::fr")
    assert completion_items(unknown_response) == []

    intrinsic = "fn main() -> Unit { UInt::frfoo; }\n"
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": intrinsic}],
            },
        )
    )
    intrinsic_response = completion(server, uri, 3, intrinsic, "UInt::fr")
    assert [item["label"] for item in completion_items(intrinsic_response)] == [
        "from"
    ]

def test_import_namespace_filter_preserves_uint_conversion_constants() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    provider_uri = "file:///workspace/provider.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, provider_uri, "fn helper() -> Unit { return (); }\n")
    caller = (
        "import * as api from ./provider.nova;\n"
        "fn main(i: Int, u: UInt) -> Unit { Int::from_uint(U); }\n"
    )
    open_nova(server, caller_uri, caller)

    response = completion(server, caller_uri, 10, caller, "Int::from_uint(U")
    labels = [item["label"] for item in completion_items(response)]

    assert labels == ["UInt::MAX", "UInt::MIN"]
    assert all(not label.startswith("api::") for label in labels)
