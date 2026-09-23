from __future__ import annotations

from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    item_defaults: list[str] | None = None,
    insert_replace: bool = False,
    snippets: bool = False,
    resolve_detail: bool = False,
    position_encoding: str | None = None,
) -> None:
    completion_item: dict[str, Any] = {
        "insertReplaceSupport": insert_replace,
        "snippetSupport": snippets,
    }
    if resolve_detail:
        completion_item["resolveSupport"] = {"properties": ["detail"]}
    completion: dict[str, Any] = {"completionItem": completion_item}
    if item_defaults is not None:
        completion["completionList"] = {"itemDefaults": item_defaults}
    capabilities: dict[str, Any] = {
        "textDocument": {"completion": completion},
    }
    if position_encoding is not None:
        capabilities["general"] = {"positionEncodings": [position_encoding]}
    response = server.handle(
        request("initialize", 1, {"capabilities": capabilities})
    )
    assert response is not None


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


def completion(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    line: int,
    character: int,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
    )
    assert response is not None
    return response


def items(response: dict[str, Any]) -> list[dict[str, Any]]:
    result = response["result"]
    if isinstance(result, list):
        return result
    assert isinstance(result, dict)
    item_list = result.get("items")
    assert isinstance(item_list, list)
    return item_list


def by_label(response: dict[str, Any], label: str) -> dict[str, Any]:
    return next(item for item in items(response) if item["label"] == label)


def source_text() -> str:
    return (
        "fn target(value: Int) -> Int { return value; }\n"
        "fn main() -> Unit {\n"
        "  tarfoo;\n"
        "}\n"
    )


def test_completion_list_shares_plain_edit_range_when_supported() -> None:
    server = NovaProductLanguageServer()
    initialize(server, item_defaults=["editRange"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    response = completion(server, uri, 2, line=2, character=5)

    assert response["result"]["isIncomplete"] is False
    assert response["result"]["itemDefaults"] == {
        "editRange": {
            "start": {"line": 2, "character": 2},
            "end": {"line": 2, "character": 8},
        }
    }
    target = by_label(response, "target")
    assert "textEdit" not in target
    assert "textEditText" not in target
    assert "insertText" not in target


def test_completion_list_shares_insert_replace_ranges_and_snippet_text() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        item_defaults=["editRange"],
        insert_replace=True,
        snippets=True,
    )
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    response = completion(server, uri, 2, line=2, character=5)

    assert response["result"]["itemDefaults"] == {
        "editRange": {
            "insert": {
                "start": {"line": 2, "character": 2},
                "end": {"line": 2, "character": 5},
            },
            "replace": {
                "start": {"line": 2, "character": 2},
                "end": {"line": 2, "character": 8},
            },
        }
    }
    target = by_label(response, "target")
    assert target["textEditText"] == "target(${1:value})$0"
    assert target["insertTextFormat"] == 2
    assert "insertText" not in target
    assert "textEdit" not in target


def test_unnegotiated_edit_range_keeps_existing_array_and_item_edit() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        item_defaults=["data"],
        insert_replace=True,
        snippets=True,
    )
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    response = completion(server, uri, 2, line=2, character=5)

    assert isinstance(response["result"], list)
    target = by_label(response, "target")
    assert target["textEdit"]["newText"] == "target(${1:value})$0"
    assert "textEditText" not in target


def test_completion_item_defaults_compose_with_lazy_detail_resolve() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        item_defaults=["editRange"],
        insert_replace=True,
        snippets=True,
        resolve_detail=True,
    )
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, source_text())

    response = completion(server, uri, 2, line=2, character=5)
    target = by_label(response, "target")
    assert "detail" not in target
    assert target["textEditText"] == "target(${1:value})$0"
    assert isinstance(target.get("data"), dict)

    resolved = server.handle(request("completionItem/resolve", 3, target))
    assert resolved is not None
    assert resolved["result"]["detail"] == "fn target(value: Int) -> Int"
    assert resolved["result"]["textEditText"] == "target(${1:value})$0"


def test_numeric_intrinsic_completion_uses_shared_edit_range() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        item_defaults=["editRange"],
        insert_replace=True,
        snippets=True,
    )
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() -> Unit {\n  UInt::frfoo;\n}\n")

    response = completion(server, uri, 2, line=1, character=10)

    assert response["result"]["itemDefaults"]["editRange"] == {
        "insert": {
            "start": {"line": 1, "character": 8},
            "end": {"line": 1, "character": 10},
        },
        "replace": {
            "start": {"line": 1, "character": 8},
            "end": {"line": 1, "character": 13},
        },
    }
    conversion = by_label(response, "from")
    assert conversion["textEditText"] == "from(${1:value})$0"
    assert conversion["insertTextFormat"] == 2


@pytest.mark.parametrize(
    ("encoding", "cursor", "start", "end"),
    [
        ("utf-8", 13, 10, 16),
        ("utf-16", 11, 8, 14),
        ("utf-32", 10, 7, 13),
    ],
)
def test_shared_edit_range_uses_negotiated_position_units(
    encoding: str,
    cursor: int,
    start: int,
    end: int,
) -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        item_defaults=["editRange"],
        insert_replace=True,
        position_encoding=encoding,
    )
    uri = "file:///workspace/main.nova"
    text = (
        "fn target() -> Unit { return (); }\n"
        "fn main() -> Unit {\n"
        '  "😀"; tarfoo;\n'
        "}\n"
    )
    open_nova(server, uri, text)

    response = completion(server, uri, 2, line=2, character=cursor)

    assert response["result"]["itemDefaults"]["editRange"] == {
        "insert": {
            "start": {"line": 2, "character": start},
            "end": {"line": 2, "character": cursor},
        },
        "replace": {
            "start": {"line": 2, "character": start},
            "end": {"line": 2, "character": end},
        },
    }
