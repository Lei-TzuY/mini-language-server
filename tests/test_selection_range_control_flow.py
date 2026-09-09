from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int = 1, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize(server: NovaProductLanguageServer) -> None:
    response = server.handle(
        request(
            "initialize",
            params={"capabilities": {"textDocument": {"selectionRange": {}}}},
        )
    )
    assert response is not None


def open_document(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    version: int = 1,
) -> None:
    server.handle(
        notification(
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


def selection_range(
    server: NovaProductLanguageServer,
    uri: str,
    line: int,
    character: int,
) -> dict:
    response = server.handle(
        request(
            "textDocument/selectionRange",
            request_id=2,
            params={
                "textDocument": {"uri": uri},
                "positions": [{"line": line, "character": character}],
            },
        )
    )
    assert response is not None
    return response["result"][0]


def ranges(item: dict) -> list[dict]:
    result = []
    current = item
    while True:
        result.append(current["range"])
        parent = current.get("parent")
        if not isinstance(parent, dict):
            return result
        current = parent


def test_selection_range_includes_nested_control_flow_parents() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(flag: Bool) {\n"
        "  if (flag) {\n"
        "    while (flag) {\n"
        "      print(flag)\n"
        "    }\n"
        "  } else {\n"
        "    print(flag)\n"
        "  }\n"
        "}\n"
    )
    open_document(server, uri, text)

    nested = ranges(selection_range(server, uri, 3, 13))
    assert nested == [
        {
            "start": {"line": 3, "character": 12},
            "end": {"line": 3, "character": 16},
        },
        {
            "start": {"line": 2, "character": 4},
            "end": {"line": 4, "character": 5},
        },
        {
            "start": {"line": 1, "character": 2},
            "end": {"line": 5, "character": 3},
        },
        {
            "start": {"line": 0, "character": 0},
            "end": {"line": 8, "character": 1},
        },
        {
            "start": {"line": 0, "character": 0},
            "end": {"line": 9, "character": 0},
        },
    ]

    alternate = ranges(selection_range(server, uri, 6, 11))
    assert alternate[1] == {
        "start": {"line": 5, "character": 4},
        "end": {"line": 7, "character": 3},
    }
    assert alternate[2] == {
        "start": {"line": 0, "character": 0},
        "end": {"line": 8, "character": 1},
    }


def test_selection_range_masks_control_flow_inside_comments_and_strings() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() {\n"
        "  // if (fake) {\n"
        "  let text = \"while (fake) {\"\n"
        "  print(text)\n"
        "}\n"
    )
    open_document(server, uri, text)

    parents = ranges(selection_range(server, uri, 3, 9))
    assert len(parents) == 3
    assert parents[1] == {
        "start": {"line": 0, "character": 0},
        "end": {"line": 4, "character": 1},
    }


def test_control_flow_selection_ranges_follow_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(
        server,
        uri,
        "fn main(flag: Bool) {\n  if (flag) {\n    print(flag)\n  }\n}\n",
    )

    initial = ranges(selection_range(server, uri, 2, 11))
    assert initial[1]["start"] == {"line": 1, "character": 2}

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "text": (
                            "fn main(flag: Bool) {\n"
                            "  while (flag) {\n"
                            "    print(flag)\n"
                            "  }\n"
                            "}\n"
                        )
                    }
                ],
            },
        )
    )
    changed = ranges(selection_range(server, uri, 2, 11))
    assert changed[1]["start"] == {"line": 1, "character": 2}

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(
        server,
        uri,
        "fn main(flag: Bool) {\n  if (flag) {\n    while (flag) {\n      print(flag)\n    }\n  }\n}\n",
        version=1,
    )
    reopened = ranges(selection_range(server, uri, 3, 13))
    assert reopened[1]["start"] == {"line": 2, "character": 4}
    assert reopened[2]["start"] == {"line": 1, "character": 2}
