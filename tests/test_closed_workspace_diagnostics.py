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


def test_closed_text_document_pull_returns_detached_report(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn main() { missing(); let local: Int = "bad"; }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            9,
            {"textDocument": {"uri": uri}},
        )
    )

    assert response is not None
    report = response["result"]
    assert report["kind"] == "full"
    assert report["resultId"].startswith("null:")
    assert [item["code"] for item in report["items"]] == [
        "nova.unresolved-function",
        "nova.local-type",
    ]
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_text_document_pull_supports_unchanged_result(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn main() { missing(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    first = server.handle(
        request(
            "textDocument/diagnostic",
            9,
            {"textDocument": {"uri": uri}},
        )
    )
    assert first is not None
    result_id = first["result"]["resultId"]

    second = server.handle(
        request(
            "textDocument/diagnostic",
            10,
            {
                "textDocument": {"uri": uri},
                "previousResultId": result_id,
            },
        )
    )

    assert second == {
        "jsonrpc": "2.0",
        "id": 10,
        "result": {"kind": "unchanged", "resultId": result_id},
    }


def test_closed_text_document_pull_rebinds_cross_file_type_evidence(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        'fn make() -> String { return "bad"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn main() { let local: Int = make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = server.handle(
        request(
            "textDocument/diagnostic",
            9,
            {"textDocument": {"uri": caller_uri}},
        )
    )
    assert first is not None
    assert any(
        item["code"] == "nova.local-type"
        for item in first["result"]["items"]
    )

    provider.write_text(
        "fn make() -> Int { return 1; }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = server.handle(
        request(
            "textDocument/diagnostic",
            10,
            {"textDocument": {"uri": caller_uri}},
        )
    )
    assert second is not None
    assert all(
        item["code"] != "nova.local-type"
        for item in second["result"]["items"]
    )


def test_open_buffer_takes_over_closed_text_document_pull(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn main() { missing(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    closed = server.handle(
        request(
            "textDocument/diagnostic",
            9,
            {"textDocument": {"uri": uri}},
        )
    )
    assert closed is not None
    assert closed["result"]["resultId"].startswith("null:")

    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "fn main() {}\n",
                }
            },
        )
    )

    opened = server.handle(
        request(
            "textDocument/diagnostic",
            10,
            {"textDocument": {"uri": uri}},
        )
    )
    assert opened is not None
    assert opened["result"]["resultId"].startswith("1:")
    assert opened["result"]["items"] == []


def test_closed_text_document_pull_rejects_workspace_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn main() { missing(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_before_commit(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            source.write_text("fn main() {}\n", encoding="utf-8")
            assert server._sync_closed_workspace_files() is True

    monkeypatch.setattr(
        server.requests,
        "checkpoint",
        replace_before_commit,
    )

    assert server.handle(
        request(
            "textDocument/diagnostic",
            9,
            {"textDocument": {"uri": uri}},
        )
    ) == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32801, "message": "Content modified"},
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


def test_closed_same_file_literal_argument_type_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn target(value: Int) {} fn caller() { target("wrong"); }\n',
        encoding="utf-8",
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
            "nova.argument-type",
            "argument 1 to 'target' has type 'String'; expected 'Int'",
        )
    ]
    assert server.diagnostics.get(uri) is None


def test_closed_cross_file_literal_argument_type_uses_captured_signature(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn target(flag: Bool, label: String) {}\n",
        encoding="utf-8",
    )
    caller.write_text(
        'fn caller() { target(1, "ok"); }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    reports = reports_by_uri(workspace_diagnostics(server))
    caller_report = reports[caller.absolute().as_uri()]

    assert [
        (item["code"], item["message"])
        for item in caller_report["items"]
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'Bool'",
        )
    ]


def test_closed_matching_literal_argument_type_stays_clean(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn target(value: String) {} fn caller() { target("a,b"); }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.argument-type" for item in report["items"])
    assert all(item["code"] != "nova.argument-count" for item in report["items"])


def test_closed_unknown_argument_expression_does_not_guess_type(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = 1 + 2 target(local) }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.argument-type" for item in report["items"])


def test_closed_ambiguous_target_does_not_emit_argument_type(tmp_path: Path) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_text("fn target(value: Int) {}\n", encoding="utf-8")
    second.write_text("fn target(value: String) {}\n", encoding="utf-8")
    caller.write_text('fn caller() { target(true); }\n', encoding="utf-8")
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert [item["code"] for item in report["items"]] == [
        "nova.ambiguous-function"
    ]


def test_closed_count_mismatch_does_not_stack_argument_type(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn target(value: Int, other: Int) {} '
        'fn caller() { target("wrong"); }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [item["code"] for item in report["items"]] == [
        "nova.argument-count"
    ]


def test_closed_typed_parameter_reference_reports_argument_type(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: String) {} "
        "fn caller(input: Int) { target(input); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'String'",
        )
    ]


def test_closed_literal_initialized_local_reports_argument_type(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = 1 target(local) }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [item["code"] for item in report["items"]] == [
        "nova.argument-type"
    ]


def test_closed_local_alias_chain_reports_argument_type(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: Bool) {} "
        "fn caller(input: Int) { "
        "let first = input let second = first target(second) }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'Bool'",
        )
    ]


def test_closed_explicit_local_annotation_reports_argument_type(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: Int) {} "
        'fn caller() { let local: String = "x"; target(local); }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'String'; expected 'Int'",
        )
    ]


def test_closed_local_alias_cycle_remains_conservative(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: String) {} "
        "fn caller() { let left = right; let right = left; target(right); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.argument-type" for item in report["items"])


def test_closed_cross_file_target_uses_caller_parameter_reference_type(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn target(value: String) {}\n",
        encoding="utf-8",
    )
    caller.write_text(
        "fn caller(input: Int) { target(input); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'String'",
        )
    ]

def test_closed_function_call_initialized_local_reports_argument_type(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn make() -> Int { return 1; } "
        "fn target(value: String) {} "
        "fn caller() { let local = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.argument-type"
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'String'",
        )
    ]


def test_closed_cross_file_function_call_local_uses_captured_result_type(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn make() -> Bool { return true; }\n",
        encoding="utf-8",
    )
    caller.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.argument-type"
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Bool'; expected 'String'",
        )
    ]


def test_closed_local_alias_chain_can_root_in_function_call(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn make() -> Int { return 1; } "
        "fn target(value: Bool) {} "
        "fn caller() { "
        "let first = make(); let second = first; target(second); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.argument-type"
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'Bool'",
        )
    ]


def test_closed_explicit_local_annotation_wins_over_call_initializer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn make() -> Int { return 1; } "
        "fn target(value: Int) {} "
        "fn caller() { let local: String = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.argument-type"
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'String'; expected 'Int'",
        )
    ]


def test_closed_ambiguous_call_initializer_does_not_guess_local_type(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_text("fn make() -> Int { return 1; }\n", encoding="utf-8")
    second.write_text('fn make() -> String { return "x"; }\n', encoding="utf-8")
    caller.write_text(
        "fn target(value: Bool) {} "
        "fn caller() { let local = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert "nova.ambiguous-function" in [
        item["code"] for item in report["items"]
    ]
    assert all(
        item["code"] != "nova.argument-type"
        for item in report["items"]
    )


def test_closed_unannotated_call_initializer_remains_conservative(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn make() { return 1; } "
        "fn target(value: String) {} "
        "fn caller() { let local = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(
        item["code"] != "nova.argument-type"
        for item in report["items"]
    )


def test_closed_call_local_rebinds_after_result_annotation_change(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn make() -> Int { return 1; }\n",
        encoding="utf-8",
    )
    caller.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert any(item["code"] == "nova.argument-type" for item in first["items"])

    provider.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert all(
        item["code"] != "nova.argument-type"
        for item in second["items"]
    )

def test_closed_call_local_internal_inference_uses_captured_snapshot(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn make() -> Int { return 1; } "
        "fn target(value: String) {} "
        "fn caller() { let local = make(); target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    snapshots = server.workspace_symbols.snapshots()
    snapshot = next(item for item in snapshots if item.uri == source.absolute().as_uri())
    functions: dict[str, list[tuple[Any, Any]]] = {}
    for item in snapshots:
        for symbol in item.symbols.symbols:
            if symbol.kind == "function":
                functions.setdefault(symbol.name, []).append((item, symbol))

    make_snapshot, make_symbol = functions["make"][0]
    assert server._closed_function_result_type(
        make_snapshot,
        make_symbol.span,
    ) == "Int"

    local = next(
        symbol
        for symbol in snapshot.symbols.symbols
        if symbol.kind == "variable" and symbol.name == "local"
    )
    assert server._closed_local_type(
        snapshot,
        local,
        frozenset(),
        functions,
    ) == "Int"

def test_closed_arithmetic_local_reports_argument_type(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = 1 + 2; target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.argument-type"
    ] == [
        (
            "nova.argument-type",
            "argument 1 to 'target' has type 'Int'; expected 'String'",
        )
    ]


def test_closed_string_concatenation_local_reports_argument_type(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn target(value: Int) {} '
        'fn caller() { let local = "a" + "b"; target(local); }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.argument-type"
        and item["message"]
        == "argument 1 to 'target' has type 'String'; expected 'Int'"
        for item in report["items"]
    )


def test_closed_comparison_local_reports_argument_type(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: Int) {} "
        "fn caller() { let local = 1 < 2; target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.argument-type"
        and item["message"]
        == "argument 1 to 'target' has type 'Bool'; expected 'Int'"
        for item in report["items"]
    )


def test_closed_logical_local_uses_typed_reference_operand(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: Int) {} "
        "fn caller(flag: Bool) { "
        "let local = flag && true; target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.argument-type"
        and item["message"]
        == "argument 1 to 'target' has type 'Bool'; expected 'Int'"
        for item in report["items"]
    )


def test_closed_expression_local_uses_captured_call_operand(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn make() -> Int { return 1; }\n", encoding="utf-8")
    caller.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = make() + 1; target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.argument-type"
        and item["message"]
        == "argument 1 to 'target' has type 'Int'; expected 'String'"
        for item in report["items"]
    )


def test_closed_expression_local_rebinds_after_captured_call_result_change(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn make() -> Int { return 1; }\n", encoding="utf-8")
    caller.write_text(
        "fn target(value: String) {} "
        "fn caller() { let local = make() + 1; target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert any(item["code"] == "nova.argument-type" for item in first["items"])

    provider.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert all(
        item["code"] != "nova.argument-type"
        for item in second["items"]
    )


def test_closed_mixed_expression_local_remains_conservative(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn target(value: String) {} "
        "fn caller(input) { let local = input + 1; target(local); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.argument-type" for item in report["items"])

def test_closed_return_literal_type_mismatch_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn value() -> Int { return "bad"; }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.return-type"
    ] == [
        (
            "nova.return-type",
            "return type mismatch: expected 'Int', got 'String'",
        )
    ]


def test_closed_return_reference_type_mismatch_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn value(input: Bool) -> String { return input; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.return-type"
        and item["message"]
        == "return type mismatch: expected 'String', got 'Bool'"
        for item in report["items"]
    )


def test_closed_return_local_expression_type_mismatch_is_reported(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn value() -> String { let local = 1 + 2; return local; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.return-type"
        and item["message"]
        == "return type mismatch: expected 'String', got 'Int'"
        for item in report["items"]
    )


def test_closed_return_cross_file_call_uses_captured_result_type(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn value() -> Int { return make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.return-type"
        and item["message"]
        == "return type mismatch: expected 'Int', got 'String'"
        for item in report["items"]
    )


def test_closed_return_call_rebinds_after_provider_result_change(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn value() -> Int { return make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert any(item["code"] == "nova.return-type" for item in first["items"])

    provider.write_text(
        "fn make() -> Int { return 1; }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert all(item["code"] != "nova.return-type" for item in second["items"])


def test_closed_return_ambiguous_call_remains_conservative(tmp_path: Path) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_text("fn make() -> Int { return 1; }\n", encoding="utf-8")
    second.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn value() -> Bool { return make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.return-type" for item in report["items"])
    assert any(item["code"] == "nova.ambiguous-function" for item in report["items"])

def test_closed_explicit_local_literal_mismatch_is_reported(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        'fn caller() { let local: Int = "bad"; }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert [
        (item["code"], item["message"])
        for item in report["items"]
        if item["code"] == "nova.local-type"
    ] == [
        (
            "nova.local-type",
            "local type mismatch: expected 'Int', got 'String'",
        )
    ]


def test_closed_explicit_local_reference_mismatch_is_reported(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn caller(flag: Bool) { let local: String = flag; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.local-type"
        and item["message"]
        == "local type mismatch: expected 'String', got 'Bool'"
        for item in report["items"]
    )


def test_closed_explicit_local_expression_mismatch_is_reported(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn caller() { let local: String = 1 + 2; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.local-type"
        and item["message"]
        == "local type mismatch: expected 'String', got 'Int'"
        for item in report["items"]
    )


def test_closed_explicit_local_cross_file_call_uses_captured_result(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn caller() { let local: Int = make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert any(
        item["code"] == "nova.local-type"
        and item["message"]
        == "local type mismatch: expected 'Int', got 'String'"
        for item in report["items"]
    )


def test_closed_explicit_local_call_rebinds_after_provider_result_change(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn caller() { let local: Int = make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert any(item["code"] == "nova.local-type" for item in first["items"])

    provider.write_text(
        "fn make() -> Int { return 1; }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert all(item["code"] != "nova.local-type" for item in second["items"])


def test_closed_explicit_local_ambiguous_call_remains_conservative(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_text("fn make() -> Int { return 1; }\n", encoding="utf-8")
    second.write_text(
        'fn make() -> String { return "x"; }\n',
        encoding="utf-8",
    )
    caller.write_text(
        "fn caller() { let local: Bool = make(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        caller.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.local-type" for item in report["items"])
    assert any(item["code"] == "nova.ambiguous-function" for item in report["items"])


def test_closed_explicit_local_matching_type_remains_clean(tmp_path: Path) -> None:
    source = tmp_path / "closed.nova"
    source.write_text(
        "fn caller(flag: Bool) { let local: Bool = flag && true; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    assert all(item["code"] != "nova.local-type" for item in report["items"])
