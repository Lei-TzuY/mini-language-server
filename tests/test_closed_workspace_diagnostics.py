from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def notify(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialized_server(
    root: Path,
    *,
    did_create: bool = False,
    did_delete: bool = False,
    related_information: bool = False,
    refresh_support: bool = False,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    workspace: dict[str, Any] = {"workspaceFolders": True}
    if refresh_support:
        workspace["diagnostics"] = {"refreshSupport": True}
    file_operations: dict[str, bool] = {}
    if did_create:
        file_operations["didCreate"] = True
    if did_delete:
        file_operations["didDelete"] = True
    if file_operations:
        workspace["fileOperations"] = file_operations

    text_document: dict[str, Any] = {"diagnostic": {}}
    if related_information:
        text_document["publishDiagnostics"] = {"relatedInformation": True}

    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": text_document,
                    "workspace": workspace,
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"}
                ],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
    return server


def workspace_diagnostics(
    server: NovaProductLanguageServer,
    *,
    request_id: int = 2,
    previous_result_ids: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "workspace/diagnostic",
            request_id,
            {"previousResultIds": previous_result_ids or []},
        )
    )
    assert response is not None
    return response


def reports_by_uri(response: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["uri"]: item
        for item in response["result"]["items"]
    }


def test_closed_file_base_diagnostics_are_workspace_pull_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "duplicate.nova"
    source.write_text("fn same() {} fn same() {}\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    report = reports_by_uri(workspace_diagnostics(server))[uri]

    assert report["version"] is None
    assert report["resultId"].startswith("null:")
    assert [item["code"] for item in report["items"]] == [
        "nova.duplicate-function",
        "nova.duplicate-function",
    ]
    assert server.documents.get(uri) is None
    assert server.semantics.get(uri) is None
    assert server.diagnostics.get(uri) is None
    assert server.drain_notifications() == []


def test_closed_call_resolution_uses_combined_workspace(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    caller.write_text(
        "fn caller() { target(); missing(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    reports = reports_by_uri(workspace_diagnostics(server))
    caller_report = reports[caller.absolute().as_uri()]
    provider_report = reports[provider.absolute().as_uri()]

    assert provider_report["items"] == []
    assert [
        (item["code"], item["message"])
        for item in caller_report["items"]
    ] == [
        (
            "nova.unresolved-function",
            "unresolved function 'missing'",
        )
    ]


def test_closed_ambiguous_call_reports_exact_candidate_locations(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_text("fn target() {}\n", encoding="utf-8")
    second.write_text("fn target() {}\n", encoding="utf-8")
    caller.write_text("fn caller() { target(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path, related_information=True)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]
    assert len(report["items"]) == 1
    diagnostic = report["items"][0]
    assert diagnostic["code"] == "nova.ambiguous-function"
    assert {
        item["location"]["uri"]
        for item in diagnostic["relatedInformation"]
    } == {
        first.absolute().as_uri(),
        second.absolute().as_uri(),
    }


def test_closed_workspace_report_supports_unchanged_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "duplicate.nova"
    source.write_text("fn same() {} fn same() {}\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[uri]
    second = reports_by_uri(
        workspace_diagnostics(
            server,
            request_id=3,
            previous_result_ids=[
                {"uri": uri, "value": first["resultId"]}
            ],
        )
    )[uri]

    assert second == {
        "uri": uri,
        "version": None,
        "kind": "unchanged",
        "resultId": first["resultId"],
    }


def test_create_delete_notifications_update_closed_workspace_reports(
    tmp_path: Path,
) -> None:
    server = initialized_server(
        tmp_path,
        did_create=True,
        did_delete=True,
    )
    assert workspace_diagnostics(server)["result"]["items"] == []

    source = tmp_path / "created.nova"
    source.write_text("fn caller() { missing(); }\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server.handle(
        notify(
            "workspace/didCreateFiles",
            {"files": [{"uri": uri}]},
        )
    )

    created = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[uri]
    assert created["version"] is None
    assert [item["code"] for item in created["items"]] == [
        "nova.unresolved-function"
    ]

    source.unlink()
    server.handle(
        notify(
            "workspace/didDeleteFiles",
            {"files": [{"uri": uri}]},
        )
    )

    assert workspace_diagnostics(
        server,
        request_id=4,
    )["result"]["items"] == []


def test_closed_text_document_pull_remains_outside_ownership(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn main() { missing(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            9,
            {"textDocument": {"uri": uri}},
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_closed_workspace_pull_rejects_workspace_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn main() { missing(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_before_commit(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 3:
            source.write_text("fn main() {}\n", encoding="utf-8")
            assert server._sync_closed_workspace_files() is True

    monkeypatch.setattr(
        server.requests,
        "checkpoint",
        replace_before_commit,
    )

    assert workspace_diagnostics(server) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }

def test_closed_pull_does_not_expose_unvalidated_unresolved_name_policy(
    tmp_path: Path,
) -> None:
    source = tmp_path / "literal.nova"
    source.write_text(
        "fn main() { let value = true; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(
        item["code"] != "nova.unresolved-name"
        for item in report["items"]
    )

def test_closed_file_create_requests_workspace_diagnostic_refresh(
    tmp_path: Path,
) -> None:
    server = initialized_server(
        tmp_path,
        did_create=True,
        refresh_support=True,
    )
    assert server.drain_server_requests() == []

    source = tmp_path / "created.nova"
    source.write_text("fn caller() { missing(); }\n", encoding="utf-8")
    uri = source.absolute().as_uri()
    server.handle(
        notify(
            "workspace/didCreateFiles",
            {"files": [{"uri": uri}]},
        )
    )

    queued = server.drain_server_requests()
    assert len(queued) == 1
    assert queued[0]["method"] == "workspace/diagnostic/refresh"


def test_closed_same_file_argument_count_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_bytes(
        b"fn target(value: Int) -> Int { return value; } "
        b"fn caller() { target(); }\n"
    )
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    report = reports_by_uri(workspace_diagnostics(server))[uri]

    assert report["version"] is None
    assert [
        (item["code"], item["message"])
        for item in report["items"]
    ] == [
        (
            "nova.argument-count",
            "function 'target' expects 1 argument(s) but got 0",
        )
    ]
    assert server.diagnostics.get(uri) is None


def test_closed_cross_file_argument_count_uses_exact_target_signature(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_bytes(
        b"fn target(first: Int, second: Int) -> Int { return first; }\n"
    )
    caller.write_bytes(b"fn caller() { target(1); }\n")
    server = initialized_server(tmp_path)

    reports = reports_by_uri(workspace_diagnostics(server))
    caller_report = reports[caller.absolute().as_uri()]
    provider_report = reports[provider.absolute().as_uri()]

    assert provider_report["items"] == []
    assert [
        (item["code"], item["message"])
        for item in caller_report["items"]
    ] == [
        (
            "nova.argument-count",
            "function 'target' expects 2 argument(s) but got 1",
        )
    ]


def test_closed_ambiguous_call_does_not_guess_argument_count(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_bytes(b"fn target(value: Int) {}\n")
    second.write_bytes(b"fn target() {}\n")
    caller.write_bytes(b"fn caller() { target(1, 2); }\n")
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert [item["code"] for item in report["items"]] == [
        "nova.ambiguous-function"
    ]


def test_closed_argument_count_uses_literal_aware_call_boundaries(
    tmp_path: Path,
) -> None:
    source = tmp_path / "literal.nova"
    source.write_bytes(
        b"fn target(value: String) {} "
        b'fn caller() { target("a,b"); }\n'
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(
        item["code"] != "nova.argument-count"
        for item in report["items"]
    )
