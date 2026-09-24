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
    tag_values: list[int] | None = None,
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
    publish_diagnostics: dict[str, Any] = {}
    if related_information:
        publish_diagnostics["relatedInformation"] = True
    if tag_values is not None:
        publish_diagnostics["tagSupport"] = {"valueSet": tag_values}
    if publish_diagnostics:
        text_document["publishDiagnostics"] = publish_diagnostics

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

def test_closed_text_document_pull_rejects_unindexed_uri(tmp_path: Path) -> None:
    server = initialized_server(tmp_path)
    uri = (tmp_path / "missing.nova").absolute().as_uri()

    assert server.handle(
        request(
            "textDocument/diagnostic",
            40,
            {"textDocument": {"uri": uri}},
        )
    ) == {
        "jsonrpc": "2.0",
        "id": 40,
        "error": {"code": -32602, "message": "Invalid params"},
    }


def test_closed_text_document_pull_honors_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn main() { missing(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(
        server.requests,
        "checkpoint",
        cancel_before_checkpoint,
    )

    assert server.handle(
        request(
            "textDocument/diagnostic",
            41,
            {"textDocument": {"uri": uri}},
        )
    ) == {
        "jsonrpc": "2.0",
        "id": 41,
        "error": {"code": -32800, "message": "Request cancelled"},
    }

def test_closed_document_pull_returns_closed_related_documents(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.nova"
    second = tmp_path / "b.nova"
    caller = tmp_path / "main.nova"
    first.write_text("fn target() { missing(); }\n", encoding="utf-8")
    second.write_text("fn target() {}\n", encoding="utf-8")
    caller.write_text("fn main() { target(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path, related_information=True)

    response = server.handle(
        request(
            "textDocument/diagnostic",
            50,
            {"textDocument": {"uri": caller.absolute().as_uri()}},
        )
    )
    assert response is not None
    report = response["result"]
    related = report["relatedDocuments"]
    first_uri = first.absolute().as_uri()
    second_uri = second.absolute().as_uri()
    assert list(related) == [first_uri, second_uri]
    assert related[first_uri]["kind"] == "full"
    assert related[first_uri]["resultId"].startswith("null:")
    assert [item["code"] for item in related[first_uri]["items"]] == [
        "nova.unresolved-function"
    ]
    assert related[second_uri]["kind"] == "full"
    assert related[second_uri]["resultId"].startswith("null:")
    assert related[second_uri]["items"] == []


def test_closed_document_pull_streams_related_documents(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.nova"
    second = tmp_path / "b.nova"
    caller = tmp_path / "main.nova"
    first.write_text("fn target() {}\n", encoding="utf-8")
    second.write_text("fn target() {}\n", encoding="utf-8")
    caller.write_text("fn main() { target(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path, related_information=True)
    server.drain_notifications()

    response = server.handle(
        request(
            "textDocument/diagnostic",
            51,
            {
                "textDocument": {"uri": caller.absolute().as_uri()},
                "partialResultToken": "closed-related",
            },
        )
    )
    assert response is not None
    assert response["result"]["kind"] == "full"
    assert "relatedDocuments" not in response["result"]

    progress = [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ]
    assert len(progress) == 1
    assert progress[0]["params"]["token"] == "closed-related"
    related = progress[0]["params"]["value"]["relatedDocuments"]
    assert list(related) == [
        first.absolute().as_uri(),
        second.absolute().as_uri(),
    ]
    assert all(item["kind"] == "full" for item in related.values())


def test_closed_document_pull_supports_mixed_open_closed_related_documents(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.nova"
    second = tmp_path / "b.nova"
    caller = tmp_path / "main.nova"
    first.write_text("fn target() {}\n", encoding="utf-8")
    second.write_text("fn target() {}\n", encoding="utf-8")
    caller.write_text("fn main() { target(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path, related_information=True)
    first_uri = first.absolute().as_uri()
    second_uri = second.absolute().as_uri()
    caller_uri = caller.absolute().as_uri()

    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": first_uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "fn target() { missing_open(); }\n",
                }
            },
        )
    )

    response = server.handle(
        request(
            "textDocument/diagnostic",
            52,
            {"textDocument": {"uri": caller_uri}},
        )
    )
    assert response is not None
    related = response["result"]["relatedDocuments"]
    assert related[first_uri]["resultId"].startswith("1:")
    assert [item["code"] for item in related[first_uri]["items"]] == [
        "nova.unresolved-function"
    ]
    assert related[second_uri]["resultId"].startswith("null:")
    assert related[second_uri]["items"] == []


def test_closed_document_pull_rejects_open_related_document_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "a.nova"
    second = tmp_path / "b.nova"
    caller = tmp_path / "main.nova"
    first.write_text("fn target() {}\n", encoding="utf-8")
    second.write_text("fn target() {}\n", encoding="utf-8")
    caller.write_text("fn main() { target(); }\n", encoding="utf-8")
    server = initialized_server(tmp_path, related_information=True)
    first_uri = first.absolute().as_uri()
    caller_uri = caller.absolute().as_uri()

    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": first_uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "fn target() {}\n",
                }
            },
        )
    )
    server.drain_notifications()
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_before_commit(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            server.handle(
                notify(
                    "textDocument/didChange",
                    {
                        "textDocument": {"uri": first_uri, "version": 2},
                        "contentChanges": [
                            {"text": "fn target(value: Int) {}\n"}
                        ],
                    },
                )
            )

    monkeypatch.setattr(
        server.requests,
        "checkpoint",
        replace_before_commit,
    )

    assert server.handle(
        request(
            "textDocument/diagnostic",
            53,
            {"textDocument": {"uri": caller_uri}},
        )
    ) == {
        "jsonrpc": "2.0",
        "id": 53,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/progress"
    ] == []

def test_closed_missing_return_matches_structural_branch_proof(tmp_path: Path) -> None:
    source = tmp_path / "returns.nova"
    source.write_text(
        "fn empty() -> Int {}\n"
        "fn complete(flag: Bool) -> Int { "
        "if (flag) { return 1; } else { return 0; } }\n"
        "fn incomplete(flag: Bool) -> String { "
        'if (flag) { return "ok"; } }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    missing = [
        item["message"]
        for item in report["items"]
        if item["code"] == "nova.missing-return"
    ]
    assert missing == [
        "function 'empty' with return type 'Int' has no value return",
        "function 'incomplete' with return type 'String' has no value return",
    ]


def test_closed_missing_return_reuses_constant_and_loop_termination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "control.nova"
    source.write_text(
        "fn constant() -> Int { if (1 < 2) { return 1; } }\n"
        "fn diverges() -> String { while (true) { continue; } }\n"
        "fn uncertain(flag: Bool) -> Bool { while (flag) { continue; } }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    missing = [
        item["message"]
        for item in report["items"]
        if item["code"] == "nova.missing-return"
    ]
    assert missing == [
        "function 'uncertain' with return type 'Bool' has no value return"
    ]


def test_closed_tail_expression_arbitrates_missing_return(tmp_path: Path) -> None:
    source = tmp_path / "tails.nova"
    source.write_text(
        "fn good() -> Int { 7 }\n"
        'fn bad() -> Int { "wrong" }\n'
        "fn unknown(value) -> Int { value }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    return_types = [
        item["message"]
        for item in report["items"]
        if item["code"] == "nova.return-type"
    ]
    missing = [
        item["message"]
        for item in report["items"]
        if item["code"] == "nova.missing-return"
    ]
    assert return_types == [
        "return type mismatch: expected 'Int', got 'String'"
    ]
    assert missing == [
        "function 'unknown' with return type 'Int' has no value return"
    ]


def test_closed_cross_file_explicit_never_call_closes_return_obligation(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn halt() -> ! { while (true) { continue; } }\n",
        encoding="utf-8",
    )
    caller.write_text(
        "fn value() -> Int { halt(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert all(item["code"] != "nova.missing-return" for item in first["items"])

    provider.write_text(
        "fn halt() -> Unit { return (); }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert [
        item["message"]
        for item in second["items"]
        if item["code"] == "nova.missing-return"
    ] == ["function 'value' with return type 'Int' has no value return"]


def test_closed_inferred_never_effect_is_transitive_and_cycle_safe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "effects.nova"
    source.write_text(
        "fn halt() { while (true) { continue; } }\n"
        "fn wrapper() { halt(); }\n"
        "fn value() -> Int { wrapper(); }\n"
        "fn left() { right(); }\n"
        "fn right() { left(); }\n"
        "fn cyclic() -> String { left(); }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]

    missing = [
        item["message"]
        for item in report["items"]
        if item["code"] == "nova.missing-return"
    ]
    assert missing == [
        "function 'cyclic' with return type 'String' has no value return"
    ]


def test_closed_unreachable_after_return_reuses_nested_control_flow(
    tmp_path: Path,
) -> None:
    source = tmp_path / "dead_return.nova"
    source.write_text(
        "fn value() -> Int { return 1; let dead: Int = 2; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path, tag_values=[1])
    uri = source.absolute().as_uri()

    report = reports_by_uri(workspace_diagnostics(server))[uri]
    unreachable = [
        item for item in report["items"] if item["code"] == "nova.unreachable-code"
    ]

    assert len(unreachable) == 1
    assert unreachable[0]["message"] == "unreachable code after guaranteed return"
    assert unreachable[0]["tags"] == [1]
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_unreachable_tracks_nested_loop_control(tmp_path: Path) -> None:
    source = tmp_path / "nested.nova"
    source.write_text(
        "fn loop(flag: Bool) { while (flag) { break; let dead: Int = 1; } }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]
    unreachable = [
        item for item in report["items"] if item["code"] == "nova.unreachable-code"
    ]

    assert len(unreachable) == 1
    assert unreachable[0]["message"] == "unreachable code after 'break'"


def test_closed_unreachable_merges_constant_dead_branch_regions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "constant_dead.nova"
    source.write_text(
        "fn main() { if (false) { missing(); } let live: Int = 1; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path, tag_values=[1])

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]
    unreachable = [
        item for item in report["items"] if item["code"] == "nova.unreachable-code"
    ]

    assert len(unreachable) == 1
    assert unreachable[0]["message"] == "unreachable code in constant-false if body"
    assert unreachable[0]["tags"] == [1]


def test_closed_unreachable_uses_cross_file_never_effects(tmp_path: Path) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn halt() -> ! { while (true) { continue; } }\n",
        encoding="utf-8",
    )
    caller.write_text(
        "fn main() { halt(); let dead: Int = 1; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert [
        item["message"]
        for item in first["items"]
        if item["code"] == "nova.unreachable-code"
    ] == ["unreachable code after never-returning call"]

    provider.write_text(
        "fn halt() -> Unit { return (); }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert all(
        item["code"] != "nova.unreachable-code"
        for item in second["items"]
    )


def test_closed_unreachable_inferred_never_is_transitive_and_cycle_safe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "effects.nova"
    source.write_text(
        "fn halt() { while (true) { continue; } }\n"
        "fn wrapper() { halt(); }\n"
        "fn caller() { wrapper(); let dead: Int = 1; }\n"
        "fn left() { right(); }\n"
        "fn right() { left(); }\n"
        "fn cyclic() { left(); let live: Int = 1; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]
    unreachable = [
        item for item in report["items"] if item["code"] == "nova.unreachable-code"
    ]

    assert [item["message"] for item in unreachable] == [
        "unreachable code after never-returning call"
    ]


def test_closed_uninitialized_read_tracks_direct_assignment(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.nova"
    valid = tmp_path / "valid.nova"
    invalid.write_text(
        "fn main() { var value: Int; let copy = value; }\n",
        encoding="utf-8",
    )
    valid.write_text(
        "fn main() { var value: Int; value = 1; let copy = value; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    reports = reports_by_uri(workspace_diagnostics(server))
    invalid_items = reports[invalid.absolute().as_uri()]["items"]
    valid_items = reports[valid.absolute().as_uri()]["items"]

    assert [
        item["code"]
        for item in invalid_items
        if item["code"] == "nova.uninitialized-read"
    ] == ["nova.uninitialized-read"]
    assert all(item["code"] != "nova.uninitialized-read" for item in valid_items)


def test_closed_definite_initialization_reuses_nested_if_else_join(
    tmp_path: Path,
) -> None:
    complete = tmp_path / "complete.nova"
    incomplete = tmp_path / "incomplete.nova"
    complete.write_text(
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } else { value = 2; } "
        "let copy = value; }\n",
        encoding="utf-8",
    )
    incomplete.write_text(
        "fn main(flag: Bool) { var value: Int; "
        "if flag { value = 1; } let copy = value; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    reports = reports_by_uri(workspace_diagnostics(server))
    assert all(
        item["code"] != "nova.uninitialized-read"
        for item in reports[complete.absolute().as_uri()]["items"]
    )
    reads = [
        item
        for item in reports[incomplete.absolute().as_uri()]["items"]
        if item["code"] == "nova.uninitialized-read"
    ]
    assert len(reads) == 1
    assert reads[0]["message"] == "local 'value' is read before its first assignment"


def test_closed_definite_initialization_reuses_loop_exit_join(
    tmp_path: Path,
) -> None:
    source = tmp_path / "loop.nova"
    source.write_text(
        "fn main(flag: Bool) { var value: Int; while (true) { "
        "if flag { continue; } else { value = 1; } "
        "let copy = value; break; } }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]
    assert all(
        item["code"] != "nova.uninitialized-read"
        for item in report["items"]
    )


def test_closed_definite_initialization_uses_cross_file_never_effect(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text(
        "fn halt() -> ! { while (true) { continue; } }\n",
        encoding="utf-8",
    )
    caller.write_text(
        "fn main(flag: Bool) { var value: Int; "
        "if flag { halt(); } else { value = 1; } "
        "let copy = value; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert all(
        item["code"] != "nova.uninitialized-read"
        for item in first["items"]
    )

    provider.write_text(
        "fn halt() -> Unit { return (); }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    reads = [
        item
        for item in second["items"]
        if item["code"] == "nova.uninitialized-read"
    ]
    assert len(reads) == 1


def test_closed_definite_initialization_inferred_never_is_cycle_safe(
    tmp_path: Path,
) -> None:
    source = tmp_path / "effects.nova"
    source.write_text(
        "fn halt() { while (true) { continue; } } "
        "fn wrapper() { halt(); } "
        "fn left() { right(); } fn right() { left(); } "
        "fn good(flag: Bool) { var value: Int; "
        "if flag { wrapper(); } else { value = 1; } let copy = value; } "
        "fn conservative(flag: Bool) { var value: Int; "
        "if flag { left(); } else { value = 1; } let copy = value; }\n",
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)

    report = reports_by_uri(workspace_diagnostics(server))[
        source.absolute().as_uri()
    ]
    reads = [
        item
        for item in report["items"]
        if item["code"] == "nova.uninitialized-read"
    ]
    assert len(reads) == 1
    assert reads[0]["message"] == "local 'value' is read before its first assignment"

def test_closed_assignment_and_mutability_diagnostics_use_detached_semantics(
    tmp_path: Path,
) -> None:
    source = tmp_path / "assignments.nova"
    source.write_text(
        'fn main(input: Int) { let fixed: Int = 1; fixed = "bad"; input = false; }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    report = reports_by_uri(workspace_diagnostics(server))[uri]
    assignment_items = [
        item
        for item in report["items"]
        if item["code"] in {"nova.immutable-assignment", "nova.assignment-type"}
    ]

    assert [
        (item["code"], item["message"])
        for item in assignment_items
    ] == [
        (
            "nova.immutable-assignment",
            "cannot assign to immutable local 'fixed'",
        ),
        (
            "nova.assignment-type",
            "assignment type mismatch: expected 'Int', got 'String'",
        ),
        (
            "nova.assignment-type",
            "assignment type mismatch: expected 'Int', got 'Bool'",
        ),
    ]
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_assignment_type_rebinds_cross_file_initializer_type(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn helper() -> Int { return 1; }\n", encoding="utf-8")
    caller.write_text(
        'fn main() { var value = helper(); value = "bad"; }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    caller_uri = caller.absolute().as_uri()

    first = reports_by_uri(workspace_diagnostics(server))[caller_uri]
    assert [
        item["code"]
        for item in first["items"]
        if item["code"] == "nova.assignment-type"
    ] == ["nova.assignment-type"]

    provider.write_text(
        'fn helper() -> String { return "ok"; }\n',
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = reports_by_uri(
        workspace_diagnostics(server, request_id=3)
    )[caller_uri]
    assert [
        item
        for item in second["items"]
        if item["code"] == "nova.assignment-type"
    ] == []


def test_closed_parameter_assignment_is_not_immutable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "parameter.nova"
    source.write_text(
        'fn main(input: Int) { input = "bad"; }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    report = reports_by_uri(workspace_diagnostics(server))[uri]

    assert [
        item["code"]
        for item in report["items"]
        if item["code"] in {"nova.immutable-assignment", "nova.assignment-type"}
    ] == ["nova.assignment-type"]

def test_closed_scalar_diagnostics_reuse_exact_snapshot_analyzers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scalar.nova"
    text = (
        "fn main() -> Unit { "
        "if (true) {} "
        "let quotient = 10 / 0; "
        "let converted = UInt::from(-1); "
        "return (); "
        "}\n"
    )
    source.write_text(text, encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    report = reports_by_uri(workspace_diagnostics(server))[uri]
    scalar = [
        item
        for item in report["items"]
        if item["code"]
        in {
            "nova.constant-condition",
            "nova.division-by-zero",
            "nova.conversion-range",
        }
    ]

    assert [(item["code"], item["message"]) for item in scalar] == [
        ("nova.constant-condition", "if condition is always true"),
        ("nova.division-by-zero", "integer division by zero is invalid"),
        (
            "nova.conversion-range",
            "checked conversion 'UInt::from' cannot represent constant Int value -1 as UInt",
        ),
    ]
    assert server.documents.get(uri) is None
    assert server.semantics.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_scalar_diagnostics_match_live_analyzer_results(
    tmp_path: Path,
) -> None:
    source = tmp_path / "scalar.nova"
    text = (
        "fn main() -> Unit { "
        "while (false) {} "
        "let quotient = 10 % -0; "
        "let converted = Int::from_uint(UInt::MAX); "
        "return (); "
        "}\n"
    )
    source.write_text(text, encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    closed = reports_by_uri(workspace_diagnostics(server))[uri]
    closed_scalar = [
        (item["code"], item["message"])
        for item in closed["items"]
        if item["code"]
        in {
            "nova.constant-condition",
            "nova.division-by-zero",
            "nova.conversion-range",
        }
    ]

    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 7,
                    "text": text,
                }
            },
        )
    )
    live = server.diagnostics.get(uri)
    assert live is not None
    live_scalar = [
        (item.code, item.message)
        for item in live.diagnostics
        if item.code
        in {
            "nova.constant-condition",
            "nova.division-by-zero",
            "nova.conversion-range",
        }
    ]

    assert live_scalar == closed_scalar
