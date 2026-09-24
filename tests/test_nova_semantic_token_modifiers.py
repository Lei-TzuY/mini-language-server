from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.semantic_tokens import TOKEN_TYPES
from mini_language_server.workspace import WorkspaceIndexError


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    modifiers: list[str],
    refresh_support: bool = False,
    workspace_folders: list[dict[str, str]] | None = None,
) -> list[str]:
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "semanticTokens": {
                            "requests": {"full": True, "range": True},
                            "tokenModifiers": modifiers,
                        }
                    },
                    "workspace": {
                        **(
                            {"semanticTokens": {"refreshSupport": True}}
                            if refresh_support
                            else {}
                        ),
                        **(
                            {"workspaceFolders": True}
                            if workspace_folders is not None
                            else {}
                        ),
                    },
                },
                **(
                    {"workspaceFolders": workspace_folders}
                    if workspace_folders is not None
                    else {}
                ),
            },
        )
    )
    assert response is not None
    provider = response["result"]["capabilities"]["semanticTokensProvider"]
    return provider["legend"]["tokenModifiers"]


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


def semantic_tokens(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int,
    *,
    source_range: dict[str, Any] | None = None,
) -> dict[str, Any]:
    method = (
        "textDocument/semanticTokens/range"
        if source_range is not None
        else "textDocument/semanticTokens/full"
    )
    params: dict[str, Any] = {"textDocument": {"uri": uri}}
    if source_range is not None:
        params["range"] = source_range
    result = server.handle(request(method, request_id, params))
    assert result is not None
    return result


def decode(
    data: list[int], modifier_legend: list[str]
) -> list[tuple[int, int, int, str, frozenset[str]]]:
    result: list[tuple[int, int, int, str, frozenset[str]]] = []
    line = 0
    character = 0
    for index in range(0, len(data), 5):
        delta_line, delta_start, length, token_type, modifiers = data[index : index + 5]
        line += delta_line
        character = character + delta_start if delta_line == 0 else delta_start
        names = frozenset(
            name
            for bit, name in enumerate(modifier_legend)
            if modifiers & (1 << bit)
        )
        result.append((line, character, length, TOKEN_TYPES[token_type], names))
    return result


def test_nova_reference_tokens_and_mutability_modifiers_are_negotiated() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(
        server,
        modifiers=["modification", "readonly", "declaration", "documentation"],
    )
    assert legend == ["declaration", "readonly", "modification"]
    uri = "file:///workspace/main.nova"
    text = (
        "fn main(input: Int) {\n"
        "    let fixed = input;\n"
        "    var mutable = fixed;\n"
        "    mutable = fixed;\n"
        "}\n"
    )
    open_nova(server, uri, text)

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    declaration = frozenset({"declaration"})
    immutable = frozenset({"declaration", "readonly"})
    readonly = frozenset({"readonly"})
    modification = frozenset({"modification"})

    assert (0, 3, 4, "function", declaration) in tokens
    assert (0, 8, 5, "parameter", declaration) in tokens
    assert (1, 8, 5, "variable", immutable) in tokens
    assert (1, 16, 5, "parameter", frozenset()) in tokens
    assert (2, 8, 7, "variable", declaration) in tokens
    assert (2, 18, 5, "variable", readonly) in tokens
    assert (3, 4, 7, "variable", modification) in tokens
    assert (3, 14, 5, "variable", readonly) in tokens


def test_invalid_immutable_assignment_is_readonly_and_modification() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, modifiers=["readonly", "modification"])
    assert legend == ["readonly", "modification"]
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let fixed = 1; fixed = 2; }\n")

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    assert (0, 27, 5, "variable", frozenset({"readonly", "modification"})) in tokens


def test_reference_tokens_exist_without_modifier_support() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, modifiers=[])
    assert legend == []
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn helper() {}\nfn main() { helper(); }\n")

    response = semantic_tokens(server, uri, 2)
    tokens = set(decode(response["result"]["data"], legend))
    assert (1, 12, 6, "function", frozenset()) in tokens
    assert all(not modifiers for *_, modifiers in tokens)


def test_reference_tokens_and_modifiers_respect_range_requests() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(
        server, modifiers=["declaration", "readonly", "modification"]
    )
    uri = "file:///workspace/main.nova"
    text = (
        "fn main() {\n"
        "    let fixed = 1;\n"
        "    var mutable = fixed;\n"
        "    mutable = fixed;\n"
        "}\n"
    )
    open_nova(server, uri, text)

    response = semantic_tokens(
        server,
        uri,
        2,
        source_range={
            "start": {"line": 3, "character": 0},
            "end": {"line": 4, "character": 0},
        },
    )
    tokens = decode(response["result"]["data"], legend)
    assert tokens == [
        (3, 4, 7, "variable", frozenset({"modification"})),
        (3, 14, 5, "variable", frozenset({"readonly"})),
    ]


def test_same_version_workspace_replacement_rejects_stale_reference_tokens() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=["declaration", "readonly", "modification"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let value = 1; value; }\n")
    original = server.workspace_symbols.get(uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert semantic_tokens(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_reference_semantic_tokens_honor_cancellation() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=["declaration", "readonly", "modification"])
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn main() { let value = 1; value; }\n")
    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.requests.checkpoint
    blocked = False

    def blocked_checkpoint(context):
        nonlocal blocked
        if not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(context)

    server.requests.checkpoint = blocked_checkpoint  # type: ignore[method-assign]
    thread = Thread(
        target=lambda: responses.append(semantic_tokens(server, uri, 5))
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 5}))
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 5,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]


def test_semantic_token_refresh_requested_for_cross_file_resolution_change() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, modifiers=[], refresh_support=True)
    caller_uri = "file:///workspace/caller.nova"
    library_uri = "file:///workspace/library.nova"
    caller_text = "fn caller() { target(); }\n"
    open_nova(server, caller_uri, caller_text)

    before = set(decode(semantic_tokens(server, caller_uri, 20)["result"]["data"], legend))
    assert (0, 14, 6, "function", frozenset()) not in before
    assert server.drain_server_requests() == []

    open_nova(server, library_uri, "fn target() {}\n")
    refresh = server.drain_server_requests()
    assert len(refresh) == 1
    assert refresh[0]["method"] == "workspace/semanticTokens/refresh"
    assert "params" not in refresh[0]

    after = set(decode(semantic_tokens(server, caller_uri, 21)["result"]["data"], legend))
    assert (0, 14, 6, "function", frozenset()) in after


def test_semantic_token_refresh_coalesces_until_response_then_rearms() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=[], refresh_support=True)
    caller_uri = "file:///workspace/caller.nova"
    library_uri = "file:///workspace/library.nova"
    open_nova(server, caller_uri, "fn caller() { target(); }\n")
    open_nova(server, library_uri, "fn target() {}\n")
    first = server.drain_server_requests()
    assert len(first) == 1
    first_id = first[0]["id"]

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )
    assert server.drain_server_requests() == []

    assert server.handle(
        {"jsonrpc": "2.0", "id": first_id, "result": None}
    ) is None

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 3},
                "contentChanges": [{"text": "fn target(flag: Bool) {}\n"}],
            },
        )
    )
    second = server.drain_server_requests()
    assert len(second) == 1
    assert second[0]["method"] == "workspace/semanticTokens/refresh"
    assert second[0]["id"] != first_id


def test_semantic_token_refresh_error_response_rearms_future_refresh() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=[], refresh_support=True)
    caller_uri = "file:///workspace/caller.nova"
    library_uri = "file:///workspace/library.nova"
    open_nova(server, caller_uri, "fn caller() { target(); }\n")
    open_nova(server, library_uri, "fn target() {}\n")
    refresh = server.drain_server_requests()[0]

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": refresh["id"],
            "error": {"code": -32603, "message": "refresh failed"},
        }
    ) is None

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )
    requests = server.drain_server_requests()
    assert len(requests) == 1
    assert requests[0]["method"] == "workspace/semanticTokens/refresh"


def test_semantic_token_refresh_requires_workspace_client_support() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=[], refresh_support=False)
    open_nova(server, "file:///workspace/caller.nova", "fn caller() { target(); }\n")
    open_nova(server, "file:///workspace/library.nova", "fn target() {}\n")

    assert server.drain_server_requests() == []


def test_single_document_change_does_not_send_global_semantic_token_refresh() -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=[], refresh_support=True)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn target() {}\n")

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )

    assert server.drain_server_requests() == []


def test_failed_workspace_replacement_does_not_request_semantic_token_refresh(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, modifiers=[], refresh_support=True)
    caller_uri = "file:///workspace/caller.nova"
    library_uri = "file:///workspace/library.nova"
    open_nova(server, caller_uri, "fn caller() { target(); }\n")
    open_nova(server, library_uri, "fn target() {}\n")
    first = server.drain_server_requests()[0]
    server.handle({"jsonrpc": "2.0", "id": first["id"], "result": None})

    def reject_replace(*args: Any, **kwargs: Any) -> Any:
        raise WorkspaceIndexError("stale workspace")

    monkeypatch.setattr(server.workspace_symbols, "replace", reject_replace)
    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )

    assert server.drain_server_requests() == []


def test_workspace_folder_change_requests_semantic_token_refresh() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        modifiers=[],
        refresh_support=True,
        workspace_folders=[{"uri": "file:///workspace/a", "name": "a"}],
    )
    target_uri = "file:///workspace/a/target.nova"
    caller_uri = "file:///workspace/b/caller.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target(); }\n")
    assert server.drain_server_requests() == []

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": "file:///workspace/b", "name": "b"}],
                    "removed": [],
                }
            },
        )
    )

    refresh = server.drain_server_requests()
    assert len(refresh) == 1
    assert refresh[0]["method"] == "workspace/semanticTokens/refresh"

def test_function_semantic_tokens_follow_transitive_import_visibility() -> None:
    server = NovaProductLanguageServer()
    legend = initialize(server, modifiers=[])
    helper_uri = "file:///workspace/helper.nova"
    middle_uri = "file:///workspace/middle.nova"
    other_uri = "file:///workspace/other.nova"
    caller_uri = "file:///workspace/caller.nova"
    open_nova(server, helper_uri, "fn target(value: Int) {}\n")
    open_nova(server, middle_uri, "import ./helper.nova;\nfn middle() {}\n")
    open_nova(server, other_uri, "fn target(flag: Bool) {}\n")
    caller = "import ./middle.nova;\nfn caller() { target(1) }\n"
    open_nova(server, caller_uri, caller)

    response = semantic_tokens(server, caller_uri, 90)
    tokens = set(decode(response["result"]["data"], legend))
    call_character = caller.splitlines()[1].index("target")

    assert (
        1,
        call_character,
        len("target"),
        "function",
        frozenset(),
    ) in tokens
