from typing import Any

from mini_language_server import NovaProductLanguageServer
from mini_language_server.workspace import WorkspaceIndexError


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


def initialize(
    server: NovaProductLanguageServer,
    *,
    supported: bool = True,
    refresh_support: bool = False,
    work_done_progress: bool = False,
) -> dict:
    text_document = {"diagnostic": {}} if supported else {}
    workspace = (
        {"diagnostics": {"refreshSupport": True}}
        if refresh_support
        else {}
    )
    capabilities: dict[str, Any] = {
        "textDocument": text_document,
        "workspace": workspace,
    }
    if work_done_progress:
        capabilities["window"] = {"workDoneProgress": True}
    response = server.handle(
        request(
            "initialize",
            params={"capabilities": capabilities},
        )
    )
    assert response is not None
    return response


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


def pull_diagnostics(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int = 2,
    previous_result_id: str | None = None,
) -> dict:
    params: dict[str, Any] = {"textDocument": {"uri": uri}}
    if previous_result_id is not None:
        params["previousResultId"] = previous_result_id
    response = server.handle(
        request("textDocument/diagnostic", request_id=request_id, params=params)
    )
    assert response is not None
    return response


def workspace_diagnostics(
    server: NovaProductLanguageServer,
    request_id: int = 20,
    previous_result_ids: list[dict[str, str]] | None = None,
    partial_result_token: str | int | None = None,
    work_done_token: str | int | None = None,
) -> dict:
    params: dict[str, Any] = {"previousResultIds": previous_result_ids or []}
    if partial_result_token is not None:
        params["partialResultToken"] = partial_result_token
    if work_done_token is not None:
        params["workDoneToken"] = work_done_token
    response = server.handle(
        request(
            "workspace/diagnostic",
            request_id=request_id,
            params=params,
        )
    )
    assert response is not None
    return response


def test_pull_diagnostic_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    provider = initialize(supported)["result"]["capabilities"]["diagnosticProvider"]
    assert provider == {
        "interFileDependencies": True,
        "workspaceDiagnostics": True,
    }

    unsupported = NovaProductLanguageServer()
    response = initialize(unsupported, supported=False)
    assert "diagnosticProvider" not in response["result"]["capabilities"]


def test_pull_diagnostics_return_full_then_unchanged_report() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")

    first = pull_diagnostics(server, uri)
    report = first["result"]
    assert report["kind"] == "full"
    assert report["resultId"].startswith("1:")
    assert [(item["code"], item["message"]) for item in report["items"]] == [
        ("nova.unresolved-function", "unresolved function 'missing'")
    ]

    second = pull_diagnostics(
        server,
        uri,
        request_id=3,
        previous_result_id=report["resultId"],
    )
    assert second["result"] == {
        "kind": "unchanged",
        "resultId": report["resultId"],
    }


def test_pull_diagnostics_follow_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    old_result_id = pull_diagnostics(server, uri)["result"]["resultId"]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn missing() {}\nfn main() {\n  missing()\n}\n"}
                ],
            },
        )
    )
    changed = pull_diagnostics(
        server,
        uri,
        request_id=3,
        previous_result_id=old_result_id,
    )["result"]
    assert changed["kind"] == "full"
    assert changed["resultId"].startswith("2:")
    assert changed["items"] == []

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn main() {\n  other_missing()\n}\n", version=1)
    reopened = pull_diagnostics(
        server,
        uri,
        request_id=4,
        previous_result_id=old_result_id,
    )["result"]
    assert reopened["kind"] == "full"
    assert reopened["resultId"] != old_result_id
    assert reopened["items"][0]["code"] == "nova.unresolved-function"


def test_pull_diagnostics_reject_same_version_snapshot_replacement(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_on_second_checkpoint(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_on_second_checkpoint)
    assert pull_diagnostics(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_pull_diagnostics_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert pull_diagnostics(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }


def test_workspace_diagnostics_are_deterministic_and_support_unchanged_reports() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    a_uri = "file:///workspace/a.nova"
    b_uri = "file:///workspace/b.nova"
    open_document(server, b_uri, "fn b() {}\n")
    open_document(server, a_uri, "fn a() {\n  missing()\n}\n")

    first = workspace_diagnostics(server)["result"]["items"]
    assert [item["uri"] for item in first] == [a_uri, b_uri]
    assert first[0]["kind"] == "full"
    assert first[0]["items"][0]["code"] == "nova.unresolved-function"
    assert first[1]["kind"] == "full"
    assert first[1]["items"] == []

    previous = [{"uri": item["uri"], "value": item["resultId"]} for item in first]
    second = workspace_diagnostics(server, request_id=21, previous_result_ids=previous)
    assert [item["kind"] for item in second["result"]["items"]] == [
        "unchanged",
        "unchanged",
    ]


def test_workspace_diagnostics_follow_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    old = workspace_diagnostics(server)["result"]["items"][0]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main() {}\n"}],
            },
        )
    )
    changed = workspace_diagnostics(
        server,
        request_id=21,
        previous_result_ids=[{"uri": uri, "value": old["resultId"]}],
    )["result"]["items"][0]
    assert changed["kind"] == "full"
    assert changed["version"] == 2
    assert changed["items"] == []

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn main() {\n  other_missing()\n}\n", version=1)
    reopened = workspace_diagnostics(server, request_id=22)["result"]["items"][0]
    assert reopened["resultId"] != old["resultId"]
    assert reopened["items"][0]["code"] == "nova.unresolved-function"


def test_workspace_diagnostics_reject_same_version_snapshot_replacement(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_before_publication(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 3:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_before_publication)
    assert workspace_diagnostics(server) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_diagnostics_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    open_document(server, "file:///workspace/main.nova", "fn main() {}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert workspace_diagnostics(server) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32800, "message": "Request cancelled"},
    }


def test_workspace_diagnostic_refresh_is_requested_for_cross_file_change() -> None:
    server = NovaProductLanguageServer()
    initialize(server, refresh_support=True)
    caller = "file:///workspace/caller.nova"
    library = "file:///workspace/library.nova"

    open_document(server, caller, "fn caller() { target() }\n")
    assert server.drain_server_requests() == []

    open_document(server, library, "fn target() {}\n")
    requests = server.drain_server_requests()
    assert len(requests) == 1
    refresh = requests[0]
    assert refresh["method"] == "workspace/diagnostic/refresh"
    assert "params" not in refresh
    request_id = refresh["id"]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )
    assert server.drain_server_requests() == []

    assert server.handle(
        {"jsonrpc": "2.0", "id": request_id, "result": None}
    ) is None

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 3},
                "contentChanges": [{"text": "fn target() {}\n"}],
            },
        )
    )
    second = server.drain_server_requests()
    assert len(second) == 1
    assert second[0]["method"] == "workspace/diagnostic/refresh"
    assert second[0]["id"] != request_id


def test_workspace_diagnostic_refresh_error_response_allows_future_refresh() -> None:
    server = NovaProductLanguageServer()
    initialize(server, refresh_support=True)
    caller = "file:///workspace/caller.nova"
    library = "file:///workspace/library.nova"
    open_document(server, caller, "fn caller() { target() }\n")
    open_document(server, library, "fn target() {}\n")
    refresh = server.drain_server_requests()[0]

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": refresh["id"],
            "error": {"code": -32603, "message": "refresh failed"},
        }
    ) is None

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )
    assert len(server.drain_server_requests()) == 1


def test_workspace_diagnostic_refresh_requires_client_support() -> None:
    server = NovaProductLanguageServer()
    initialize(server, refresh_support=False)
    open_document(server, "file:///workspace/a.nova", "fn target() {}\n")
    open_document(
        server,
        "file:///workspace/b.nova",
        "fn caller() { target() }\n",
    )

    assert server.drain_server_requests() == []


def test_single_document_change_does_not_send_global_diagnostic_refresh() -> None:
    server = NovaProductLanguageServer()
    initialize(server, refresh_support=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {}\n")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn main(value: Int) {}\n"}],
            },
        )
    )

    assert server.drain_server_requests() == []


def test_closing_workspace_document_requests_refresh_for_remaining_document() -> None:
    server = NovaProductLanguageServer()
    initialize(server, refresh_support=True)
    caller = "file:///workspace/caller.nova"
    library = "file:///workspace/library.nova"
    open_document(server, caller, "fn caller() { target() }\n")
    open_document(server, library, "fn target() {}\n")
    first = server.drain_server_requests()[0]
    server.handle({"jsonrpc": "2.0", "id": first["id"], "result": None})

    server.handle(
        notification(
            "textDocument/didClose",
            {"textDocument": {"uri": library}},
        )
    )

    refresh = server.drain_server_requests()
    assert len(refresh) == 1
    assert refresh[0]["method"] == "workspace/diagnostic/refresh"


def test_stale_workspace_diagnostic_commit_does_not_request_refresh(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, refresh_support=True)
    caller = "file:///workspace/caller.nova"
    library = "file:///workspace/library.nova"
    open_document(server, caller, "fn caller() { target() }\n")
    open_document(server, library, "fn target() {}\n")
    first = server.drain_server_requests()[0]
    server.handle({"jsonrpc": "2.0", "id": first["id"], "result": None})

    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def reject_workspace_publish(*args: Any, **kwargs: Any) -> Any:
        raise WorkspaceIndexError("stale workspace")

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        reject_workspace_publish,
    )
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library, "version": 2},
                "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
            },
        )
    )
    assert server.drain_server_requests() == []

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        real_commit,
    )

def initialize_with_workspace_folder(
    server: NovaProductLanguageServer,
    *,
    refresh_support: bool = False,
) -> dict:
    workspace: dict[str, Any] = {"workspaceFolders": True}
    if refresh_support:
        workspace["diagnostics"] = {"refreshSupport": True}
    response = server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "textDocument": {"diagnostic": {}},
                    "workspace": workspace,
                },
                "workspaceFolders": [
                    {"uri": "file:///workspace/a", "name": "a"},
                ],
            },
        )
    )
    assert response is not None
    return response


def test_workspace_diagnostics_include_only_scoped_open_documents() -> None:
    server = NovaProductLanguageServer()
    initialize_with_workspace_folder(server)
    in_scope = "file:///workspace/a/main.nova"
    outside = "file:///workspace/b/other.nova"
    open_document(server, in_scope, "fn main() {}\n")
    open_document(server, outside, "fn other() {}\n")

    result = workspace_diagnostics(server)["result"]["items"]

    assert [item["uri"] for item in result] == [in_scope]


def test_workspace_folder_change_requests_diagnostic_refresh_and_expands_report() -> None:
    server = NovaProductLanguageServer()
    initialize_with_workspace_folder(server, refresh_support=True)
    in_scope = "file:///workspace/a/main.nova"
    outside = "file:///workspace/b/other.nova"
    open_document(server, in_scope, "fn main() {}\n")
    open_document(server, outside, "fn other() {}\n")
    assert server.drain_server_requests() == []

    server.handle(
        notification(
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
    assert refresh[0]["method"] == "workspace/diagnostic/refresh"

    result = workspace_diagnostics(server, request_id=21)["result"]["items"]
    assert [item["uri"] for item in result] == [in_scope, outside]

def test_workspace_diagnostics_reject_workspace_folder_generation_change(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize_with_workspace_folder(server)
    in_scope = "file:///workspace/a/main.nova"
    outside = "file:///workspace/b/other.nova"
    open_document(server, in_scope, "fn main() {}\n")
    open_document(server, outside, "fn other() {}\n")
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def change_scope_before_publication(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 2:
            server.handle(
                notification(
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

    monkeypatch.setattr(
        server.requests,
        "checkpoint",
        change_scope_before_publication,
    )

    assert workspace_diagnostics(server) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }

def test_workspace_diagnostics_reject_new_in_scope_document_after_capture(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize_with_workspace_folder(server)
    first_uri = "file:///workspace/a/first.nova"
    second_uri = "file:///workspace/a/second.nova"
    open_document(server, first_uri, "fn first() {}\n")
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def open_before_publication(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 3:
            open_document(server, second_uri, "fn second() {}\n")

    monkeypatch.setattr(server.requests, "checkpoint", open_before_publication)

    assert workspace_diagnostics(server) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_diagnostics_ignore_new_out_of_scope_document_after_capture(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize_with_workspace_folder(server)
    in_scope = "file:///workspace/a/main.nova"
    outside = "file:///workspace/b/other.nova"
    open_document(server, in_scope, "fn main() {}\n")
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def open_before_publication(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 3:
            open_document(server, outside, "fn outside() {}\n")

    monkeypatch.setattr(server.requests, "checkpoint", open_before_publication)

    response = workspace_diagnostics(server)
    assert response["result"]["items"] == [
        {
            "uri": in_scope,
            "version": 1,
            "kind": "full",
            "resultId": response["result"]["items"][0]["resultId"],
            "items": [],
        }
    ]


def test_unscoped_workspace_diagnostics_reject_new_document_after_capture(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    first_uri = "file:///workspace/first.nova"
    second_uri = "file:///workspace/second.nova"
    open_document(server, first_uri, "fn first() {}\n")
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def open_before_publication(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 3:
            open_document(server, second_uri, "fn second() {}\n")

    monkeypatch.setattr(server.requests, "checkpoint", open_before_publication)

    assert workspace_diagnostics(server) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }

def test_workspace_diagnostics_emit_deterministic_partial_result_chunks() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uris = [f"file:///workspace/{index:02d}.nova" for index in range(17)]
    for index, uri in enumerate(uris):
        open_document(server, uri, f"fn item{index}() {{}}\n")
    server.drain_notifications()

    response = workspace_diagnostics(
        server,
        partial_result_token="workspace:diagnostics",
    )

    assert response["result"] == {"items": []}
    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert len(progress) == 2
    assert [item["params"]["token"] for item in progress] == [
        "workspace:diagnostics",
        "workspace:diagnostics",
    ]
    chunks = [item["params"]["value"]["items"] for item in progress]
    assert [len(chunk) for chunk in chunks] == [16, 1]
    assert [report["uri"] for chunk in chunks for report in chunk] == uris
    assert all(report["kind"] == "full" for chunk in chunks for report in chunk)


def test_workspace_diagnostic_partial_results_preserve_unchanged_reports() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    a_uri = "file:///workspace/a.nova"
    b_uri = "file:///workspace/b.nova"
    open_document(server, a_uri, "fn a() {}\n")
    open_document(server, b_uri, "fn b() {}\n")
    server.drain_notifications()

    first = workspace_diagnostics(server)["result"]["items"]
    previous = [
        {"uri": report["uri"], "value": report["resultId"]}
        for report in first
    ]

    response = workspace_diagnostics(
        server,
        request_id=21,
        previous_result_ids=previous,
        partial_result_token=7,
    )

    assert response["result"] == {"items": []}
    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert len(progress) == 1
    assert progress[0]["params"]["token"] == 7
    reports = progress[0]["params"]["value"]["items"]
    assert [report["uri"] for report in reports] == [a_uri, b_uri]
    assert [report["kind"] for report in reports] == ["unchanged", "unchanged"]


def test_workspace_diagnostic_partial_result_token_is_validated() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    open_document(server, "file:///workspace/main.nova", "fn main() {}\n")
    server.drain_notifications()

    for request_id, token in enumerate((None, True, [], {}), start=30):
        response = server.handle(
            request(
                "workspace/diagnostic",
                request_id=request_id,
                params={
                    "previousResultIds": [],
                    "partialResultToken": token,
                },
            )
        )
        assert response == {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "Invalid params"},
        }

    assert [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ] == []


def test_cancelled_workspace_diagnostic_emits_no_partial_result(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    open_document(server, "file:///workspace/main.nova", "fn main() {}\n")
    server.drain_notifications()
    real_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        real_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)

    assert workspace_diagnostics(
        server,
        partial_result_token="partial",
    ) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    assert [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ] == []


def test_stale_workspace_membership_emits_no_partial_result(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize_with_workspace_folder(server)
    first_uri = "file:///workspace/a/first.nova"
    second_uri = "file:///workspace/a/second.nova"
    open_document(server, first_uri, "fn first() {}\n")
    server.drain_notifications()
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def open_before_publication(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 3:
            open_document(server, second_uri, "fn second() {}\n")

    monkeypatch.setattr(server.requests, "checkpoint", open_before_publication)

    assert workspace_diagnostics(
        server,
        partial_result_token="partial",
    ) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ] == []

def test_workspace_diagnostic_work_done_progress_reports_lifecycle() -> None:
    server = NovaProductLanguageServer()
    initialize(server, work_done_progress=True)
    a_uri = "file:///workspace/a.nova"
    b_uri = "file:///workspace/b.nova"
    open_document(server, a_uri, "fn a() {}\n")
    open_document(server, b_uri, "fn b() {}\n")
    server.drain_notifications()

    response = workspace_diagnostics(server, work_done_token="work:diagnostics")

    assert [item["uri"] for item in response["result"]["items"]] == [a_uri, b_uri]
    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert [item["params"]["token"] for item in progress] == [
        "work:diagnostics",
        "work:diagnostics",
        "work:diagnostics",
        "work:diagnostics",
    ]
    assert [item["params"]["value"]["kind"] for item in progress] == [
        "begin",
        "report",
        "report",
        "end",
    ]
    assert progress[0]["params"]["value"] == {
        "kind": "begin",
        "title": "Workspace diagnostics",
        "cancellable": True,
        "percentage": 0,
    }
    assert progress[1]["params"]["value"]["percentage"] == 50
    assert progress[2]["params"]["value"]["percentage"] == 100
    assert progress[-1]["params"]["value"] == {
        "kind": "end",
        "message": "Workspace diagnostics complete",
    }


def test_workspace_diagnostic_work_done_and_partial_progress_compose() -> None:
    server = NovaProductLanguageServer()
    initialize(server, work_done_progress=True)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {}\n")
    server.drain_notifications()

    response = workspace_diagnostics(
        server,
        partial_result_token="diagnostic:data",
        work_done_token="diagnostic:work",
    )

    assert response["result"] == {"items": []}
    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert [
        (item["params"]["token"], item["params"]["value"].get("kind"))
        for item in progress
    ] == [
        ("diagnostic:work", "begin"),
        ("diagnostic:work", "report"),
        ("diagnostic:data", None),
        ("diagnostic:work", "end"),
    ]
    assert progress[2]["params"]["value"]["items"][0]["uri"] == uri


def test_workspace_diagnostic_work_done_requires_client_support() -> None:
    server = NovaProductLanguageServer()
    initialize(server, work_done_progress=False)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {}\n")
    server.drain_notifications()

    response = workspace_diagnostics(server, work_done_token="ignored")

    assert response["result"]["items"][0]["uri"] == uri
    assert [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ] == []


def test_workspace_diagnostic_work_done_token_is_validated() -> None:
    server = NovaProductLanguageServer()
    initialize(server, work_done_progress=True)
    open_document(server, "file:///workspace/main.nova", "fn main() {}\n")
    server.drain_notifications()

    for request_id, token in enumerate((None, True, [], {}), start=50):
        response = server.handle(
            request(
                "workspace/diagnostic",
                request_id=request_id,
                params={
                    "previousResultIds": [],
                    "workDoneToken": token,
                },
            )
        )
        assert response == {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "Invalid params"},
        }

    assert [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ] == []


def test_cancelled_workspace_diagnostic_ends_started_work_progress(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, work_done_progress=True)
    open_document(server, "file:///workspace/main.nova", "fn main() {}\n")
    server.drain_notifications()
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def cancel_after_begin(context: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_after_begin)

    assert workspace_diagnostics(server, work_done_token="work") == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert [item["params"]["value"]["kind"] for item in progress] == [
        "begin",
        "end",
    ]
    assert progress[-1]["params"]["value"]["message"] == (
        "Workspace diagnostics cancelled"
    )


def test_stale_workspace_diagnostic_ends_work_without_partial_data(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, work_done_progress=True)
    first_uri = "file:///workspace/first.nova"
    second_uri = "file:///workspace/second.nova"
    open_document(server, first_uri, "fn first() {}\n")
    server.drain_notifications()
    real_checkpoint = server.requests.checkpoint
    calls = 0

    def open_before_commit(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 3:
            open_document(server, second_uri, "fn second() {}\n")

    monkeypatch.setattr(server.requests, "checkpoint", open_before_commit)

    assert workspace_diagnostics(
        server,
        partial_result_token="data",
        work_done_token="work",
    ) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }
    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert [item["params"]["token"] for item in progress] == [
        "work",
        "work",
        "work",
    ]
    assert [item["params"]["value"]["kind"] for item in progress] == [
        "begin",
        "report",
        "end",
    ]
    assert progress[-1]["params"]["value"]["message"] == (
        "Workspace diagnostics changed"
    )

def test_document_pull_includes_direct_cross_file_related_documents() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_document(server, first_uri, "fn target() {}\n")
    open_document(server, second_uri, "fn target() {}\n")
    open_document(server, caller_uri, "fn main() { target() }\n")

    report = pull_diagnostics(server, caller_uri)["result"]
    assert report["kind"] == "full"
    assert report["items"][0]["code"] == "nova.ambiguous-function"
    assert list(report["relatedDocuments"]) == [first_uri, second_uri]
    for uri in (first_uri, second_uri):
        related = report["relatedDocuments"][uri]
        assert related["kind"] == "full"
        assert related["items"] == []
        assert related["resultId"].startswith("1:")


def test_unchanged_primary_pull_can_return_changed_related_document_report() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_document(server, first_uri, "fn target() { missing() }\n")
    open_document(server, second_uri, "fn target() {}\n")
    open_document(server, caller_uri, "fn main() { target() }\n")

    first = pull_diagnostics(server, caller_uri)["result"]
    primary_result_id = first["resultId"]
    first_related = first["relatedDocuments"][first_uri]
    assert first_related["items"][0]["code"] == "nova.unresolved-function"

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": first_uri, "version": 2},
                "contentChanges": [{"text": "fn target() {}\n"}],
            },
        )
    )
    second = pull_diagnostics(
        server,
        caller_uri,
        request_id=3,
        previous_result_id=primary_result_id,
    )["result"]
    assert second["kind"] == "unchanged"
    assert second["resultId"] == primary_result_id
    assert list(second["relatedDocuments"]) == [first_uri, second_uri]
    changed = second["relatedDocuments"][first_uri]
    assert changed["kind"] == "full"
    assert changed["items"] == []
    assert changed["resultId"] != first_related["resultId"]


def test_document_pull_rejects_related_document_change_before_commit(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_document(server, first_uri, "fn target() {}\n")
    open_document(server, second_uri, "fn target() {}\n")
    open_document(server, caller_uri, "fn main() { target() }\n")

    real_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_dependency_before_commit(context: Any) -> None:
        nonlocal calls
        calls += 1
        real_checkpoint(context)
        if calls == 2:
            server.handle(
                notification(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": first_uri, "version": 2},
                        "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
                    },
                )
            )

    monkeypatch.setattr(server.requests, "checkpoint", replace_dependency_before_commit)
    assert pull_diagnostics(server, caller_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_document_pull_rejects_related_document_change_during_render(
    monkeypatch: Any,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    first_uri = "file:///workspace/a.nova"
    second_uri = "file:///workspace/b.nova"
    caller_uri = "file:///workspace/main.nova"
    open_document(server, first_uri, "fn target() {}\n")
    open_document(server, second_uri, "fn target() {}\n")
    open_document(server, caller_uri, "fn main() { target() }\n")

    real_source_text = server._source_text
    replaced = False

    def replace_dependency_after_primary_render(text: str) -> Any:
        nonlocal replaced
        source = real_source_text(text)
        if not replaced and text == "fn main() { target() }\n":
            replaced = True
            server.handle(
                notification(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": first_uri, "version": 2},
                        "contentChanges": [{"text": "fn target(value: Int) {}\n"}],
                    },
                )
            )
        return source

    monkeypatch.setattr(server, "_source_text", replace_dependency_after_primary_render)
    assert pull_diagnostics(server, caller_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_related_documents_are_direct_and_do_not_nest_dependency_reports() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    a_uri = "file:///workspace/a.nova"
    b_uri = "file:///workspace/b.nova"
    c_uri = "file:///workspace/c.nova"
    caller_uri = "file:///workspace/main.nova"
    open_document(server, a_uri, "fn target() { helper() }\n")
    open_document(server, b_uri, "fn target() {}\n")
    open_document(server, c_uri, "fn helper() {}\n")
    open_document(server, caller_uri, "fn main() { target() }\n")

    report = pull_diagnostics(server, caller_uri)["result"]
    assert list(report["relatedDocuments"]) == [a_uri, b_uri]
    assert all(
        "relatedDocuments" not in related
        for related in report["relatedDocuments"].values()
    )
