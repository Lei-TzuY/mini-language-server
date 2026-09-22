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
) -> dict:
    text_document = {"diagnostic": {}} if supported else {}
    workspace = (
        {"diagnostics": {"refreshSupport": True}}
        if refresh_support
        else {}
    )
    response = server.handle(
        request(
            "initialize",
            params={
                "capabilities": {
                    "textDocument": text_document,
                    "workspace": workspace,
                }
            },
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
) -> dict:
    response = server.handle(
        request(
            "workspace/diagnostic",
            request_id=request_id,
            params={"previousResultIds": previous_result_ids or []},
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
