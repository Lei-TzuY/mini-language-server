from __future__ import annotations

from threading import Event, Thread
from typing import Any

import pytest

from mini_language_server.workspace import WorkspaceIndexError
from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: WorkspaceNovaLanguageServer, *, workspace_symbol: bool = True
) -> dict:
    workspace = {"symbol": {}} if workspace_symbol else {}
    result = server.handle(
        request("initialize", 1, {"capabilities": {"workspace": workspace}})
    )
    assert result is not None
    return result


def open_nova(server: WorkspaceNovaLanguageServer, uri: str, text: str) -> None:
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


def test_workspace_symbol_capability_is_negotiated() -> None:
    enabled = initialize(WorkspaceNovaLanguageServer())
    assert enabled["result"]["capabilities"]["workspaceSymbolProvider"] is True

    disabled = initialize(WorkspaceNovaLanguageServer(), workspace_symbol=False)
    assert "workspaceSymbolProvider" not in disabled["result"]["capabilities"]


def test_workspace_symbol_search_is_deterministic_across_open_nova_documents() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    open_nova(server, "file:///workspace/z.nova", "fn Zebra() {}\n")
    open_nova(server, "file:///workspace/a.nova", "fn alpha() {}\nfn beta() {}\n")

    result = server.handle(request("workspace/symbol", 2, {"query": "a"}))
    assert result is not None
    assert [(item["name"], item["location"]["uri"]) for item in result["result"]] == [
        ("alpha", "file:///workspace/a.nova"),
        ("beta", "file:///workspace/a.nova"),
        ("Zebra", "file:///workspace/z.nova"),
    ]
    assert all(item["kind"] == 12 for item in result["result"])


def test_workspace_replacement_and_close_remove_superseded_contributions() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn old() {}\n")

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn current() {}\n"}],
            },
        )
    )
    old = server.handle(request("workspace/symbol", 2, {"query": "old"}))
    current = server.handle(request("workspace/symbol", 3, {"query": "current"}))
    assert old is not None and old["result"] == []
    assert current is not None
    assert [item["name"] for item in current["result"]] == ["current"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    closed = server.handle(request("workspace/symbol", 4, {"query": "current"}))
    assert closed is not None and closed["result"] == []


def test_workspace_symbol_suppresses_same_version_replacement() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")
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
    result = server.handle(request("workspace/symbol", 2, {"query": "run"}))
    assert result == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_index_commit_rejects_superseded_parent() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")
    declarations = server.workspace_symbols.search("run")
    original = server.workspace_symbols.get(uri)
    document = server.documents.get(uri)
    assert original is not None and document is not None
    replacement = server.nova_adapter.publish(server, document)
    server.workspace_symbols.replace(replacement, expected=original)

    with pytest.raises(WorkspaceIndexError, match="replaced"):
        server.workspace_symbols.commit_if_current(declarations, lambda: None)

def initialize_with_folders(
    server: WorkspaceNovaLanguageServer,
    folders: list[dict[str, str]],
) -> dict:
    result = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "symbol": {},
                        "workspaceFolders": True,
                    }
                },
                "workspaceFolders": folders,
            },
        )
    )
    assert result is not None
    return result


def diagnostic_codes(server: WorkspaceNovaLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    ]


def test_workspace_folder_capability_and_initial_scope_are_negotiated() -> None:
    server = WorkspaceNovaLanguageServer()
    response = initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )

    assert response["result"]["capabilities"]["workspace"]["workspaceFolders"] == {
        "supported": True,
        "changeNotifications": True,
    }

    open_nova(server, "file:///workspace/a/in.nova", "fn inside() {}\n")
    open_nova(server, "file:///workspace/b/out.nova", "fn outside() {}\n")

    result = server.handle(request("workspace/symbol", 2, {"query": ""}))
    assert result is not None
    assert [
        (item["name"], item["location"]["uri"])
        for item in result["result"]
    ] == [("inside", "file:///workspace/a/in.nova")]


def test_workspace_folder_add_and_remove_rebind_open_cross_file_calls() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )
    provider = "file:///workspace/b/provider.nova"
    caller = "file:///workspace/a/caller.nova"
    open_nova(server, provider, "fn helper() {}\n")
    open_nova(server, caller, "fn main() { helper() }\n")

    assert "nova.unresolved-function" in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is None

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

    assert "nova.unresolved-function" not in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is server.semantics.get(provider)

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [],
                    "removed": [{"uri": "file:///workspace/b", "name": "b"}],
                }
            },
        )
    )

    assert "nova.unresolved-function" in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is None

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

    assert "nova.unresolved-function" not in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is server.semantics.get(provider)


def test_out_of_scope_document_keeps_local_semantics_but_not_workspace_membership() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )
    uri = "file:///outside/main.nova"
    open_nova(server, uri, "fn local() {}\n")

    assert server.semantics.get(uri) is not None
    assert server.workspace_symbols.get(uri) is None

    result = server.handle(request("workspace/symbol", 2, {"query": "local"}))
    assert result is not None
    assert result["result"] == []


def test_workspace_folder_path_boundary_does_not_match_similar_prefix() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/app", "name": "app"}],
    )
    open_nova(server, "file:///workspace/app/main.nova", "fn inside() {}\n")
    open_nova(
        server,
        "file:///workspace/application/main.nova",
        "fn prefix_collision() {}\n",
    )

    result = server.handle(request("workspace/symbol", 2, {"query": ""}))
    assert result is not None
    assert [item["name"] for item in result["result"]] == ["inside"]

def test_workspace_symbol_rejects_scope_change_after_search_capture(
    monkeypatch,
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )
    open_nova(server, "file:///workspace/a/a.nova", "fn alpha() {}\n")
    open_nova(server, "file:///workspace/b/b.nova", "fn beta() {}\n")
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def add_folder_after_search(context) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 2:
            server.handle(
                notify(
                    "workspace/didChangeWorkspaceFolders",
                    {
                        "event": {
                            "added": [
                                {"uri": "file:///workspace/b", "name": "b"}
                            ],
                            "removed": [],
                        }
                    },
                )
            )

    monkeypatch.setattr(server.requests, "checkpoint", add_folder_after_search)

    assert server.handle(request("workspace/symbol", 2, {"query": ""})) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }

def initialize_with_work_done(server: WorkspaceNovaLanguageServer) -> dict:
    result = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {"symbol": {}},
                    "window": {"workDoneProgress": True},
                }
            },
        )
    )
    assert result is not None
    return result


def workspace_symbol_source(count: int) -> str:
    return "".join(f"fn symbol_{index:02d}() {{}}\n" for index in range(count))


def test_workspace_symbol_partial_results_stream_after_exact_commit() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/many.nova"
    open_nova(server, uri, workspace_symbol_source(20))
    server.drain_notifications()

    response = server.handle(
        request(
            "workspace/symbol",
            2,
            {"query": "", "partialResultToken": "symbols"},
        )
    )

    assert response == {"jsonrpc": "2.0", "id": 2, "result": []}
    progress = server.drain_notifications()
    assert [item["params"]["token"] for item in progress] == ["symbols", "symbols"]
    assert [len(item["params"]["value"]) for item in progress] == [16, 4]
    streamed = [
        symbol
        for item in progress
        for symbol in item["params"]["value"]
    ]
    assert [item["name"] for item in streamed] == [
        f"symbol_{index:02d}" for index in range(20)
    ]


def test_workspace_symbol_without_partial_token_keeps_full_result() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    open_nova(
        server,
        "file:///workspace/main.nova",
        "fn alpha() {}\nfn beta() {}\n",
    )
    server.drain_notifications()

    response = server.handle(request("workspace/symbol", 2, {"query": ""}))

    assert response is not None
    assert [item["name"] for item in response["result"]] == ["alpha", "beta"]
    assert server.drain_notifications() == []


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("partialResultToken", True),
        ("partialResultToken", {}),
        ("workDoneToken", False),
        ("workDoneToken", []),
    ],
)
def test_workspace_symbol_rejects_invalid_progress_tokens(
    key: str, value: Any
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)

    assert server.handle(
        request("workspace/symbol", 2, {"query": "", key: value})
    ) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_workspace_symbol_reports_work_done_progress_when_negotiated() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_work_done(server)
    open_nova(
        server,
        "file:///workspace/main.nova",
        "fn alpha() {}\nfn beta() {}\n",
    )
    server.drain_notifications()

    response = server.handle(
        request(
            "workspace/symbol",
            2,
            {"query": "", "workDoneToken": "work"},
        )
    )

    assert response is not None
    assert [item["name"] for item in response["result"]] == ["alpha", "beta"]
    notifications = server.drain_notifications()
    assert [item["params"] for item in notifications] == [
        {
            "token": "work",
            "value": {
                "kind": "begin",
                "title": "Workspace symbols",
                "cancellable": True,
                "percentage": 0,
            },
        },
        {
            "token": "work",
            "value": {
                "kind": "report",
                "message": "Processed 2 of 2 workspace symbols",
                "percentage": 100,
            },
        },
        {
            "token": "work",
            "value": {
                "kind": "end",
                "message": "Workspace symbol search complete",
            },
        },
    ]


def test_workspace_symbol_ignores_work_done_output_without_client_support() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    open_nova(server, "file:///workspace/main.nova", "fn alpha() {}\n")
    server.drain_notifications()

    response = server.handle(
        request(
            "workspace/symbol",
            2,
            {"query": "", "workDoneToken": "work"},
        )
    )

    assert response is not None
    assert [item["name"] for item in response["result"]] == ["alpha"]
    assert server.drain_notifications() == []


def test_workspace_symbol_cancellation_ends_work_done_without_partial_data(
    monkeypatch: Any,
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_work_done(server)
    open_nova(server, "file:///workspace/main.nova", workspace_symbol_source(4))
    server.drain_notifications()
    entered = Event()
    release = Event()
    responses: list[dict[str, Any] | None] = []
    original = server.requests.checkpoint
    calls = 0

    def blocked_checkpoint(context) -> None:
        nonlocal calls
        calls += 1
        original(context)
        if calls == 2:
            entered.set()
            assert release.wait(timeout=5)
            original(context)

    monkeypatch.setattr(server.requests, "checkpoint", blocked_checkpoint)
    thread = Thread(
        target=lambda: responses.append(
            server.handle(
                request(
                    "workspace/symbol",
                    2,
                    {
                        "query": "",
                        "partialResultToken": "symbols",
                        "workDoneToken": "work",
                    },
                )
            )
        )
    )
    thread.start()
    assert entered.wait(timeout=5)
    server.handle(notify("$/cancelRequest", {"id": 2}))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
    notifications = server.drain_notifications()
    assert [item["params"]["token"] for item in notifications] == ["work", "work"]
    assert notifications[0]["params"]["value"]["kind"] == "begin"
    assert notifications[-1]["params"]["value"] == {
        "kind": "end",
        "message": "Workspace symbol search cancelled",
    }


def test_workspace_symbol_stale_commit_emits_no_partial_symbol_data(
    monkeypatch: Any,
) -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn alpha() {}\n")
    server.drain_notifications()
    original = server.workspace_symbols.get(uri)
    document = server.documents.get(uri)
    assert original is not None and document is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current
    injected = False

    def replace_then_commit(snapshots, callback):
        nonlocal injected
        if not injected:
            injected = True
            replacement = server.nova_adapter.publish(server, document)
            server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        replace_then_commit,
    )

    response = server.handle(
        request(
            "workspace/symbol",
            2,
            {"query": "", "partialResultToken": "symbols"},
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert server.drain_notifications() == []
