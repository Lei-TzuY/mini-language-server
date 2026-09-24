from __future__ import annotations

from pathlib import Path
from typing import Any

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
    document_changes: bool = False,
    resolve_edit: bool = False,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    code_action: dict[str, Any] = {}
    if resolve_edit:
        code_action["resolveSupport"] = {"properties": ["edit"]}
    workspace: dict[str, Any] = {"workspaceFolders": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "diagnostic": {},
                        "codeAction": code_action,
                    },
                    "workspace": workspace,
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"},
                ],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
    return server


def document_report(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    request_id: int,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/diagnostic",
            request_id,
            {"textDocument": {"uri": uri}},
        )
    )
    assert response is not None
    return response["result"]


def condition_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in report["items"]
        if item.get("code") == "nova.condition-type"
    ]


def code_actions(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    request_id: int,
    line: int,
    start: int,
    end: int,
) -> list[dict[str, Any]]:
    response = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": line, "character": start},
                    "end": {"line": line, "character": end},
                },
                "context": {"diagnostics": []},
            },
        )
    )
    assert response is not None
    return response["result"]


def test_closed_condition_diagnostics_use_captured_expression_types(
    tmp_path: Path,
) -> None:
    source = tmp_path / "conditions.nova"
    source.write_text(
        'fn main() { if (1 + 2) {} while ("value") {} if (true) {} }\n',
        encoding="utf-8",
    )
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    items = condition_items(document_report(server, uri, request_id=2))

    assert [(item["message"], item["range"]) for item in items] == [
        (
            "condition type mismatch: expected 'Bool', got 'Int'",
            {
                "start": {"line": 0, "character": 16},
                "end": {"line": 0, "character": 21},
            },
        ),
        (
            "condition type mismatch: expected 'Bool', got 'String'",
            {
                "start": {"line": 0, "character": 32},
                "end": {"line": 0, "character": 39},
            },
        ),
    ]
    assert server.documents.get(uri) is None
    assert server.semantics.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_condition_type_rebinds_cross_file_result(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_text("fn source() -> Int { return 1; }\n", encoding="utf-8")
    caller.write_text("fn main() { while (source()) {} }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = caller.absolute().as_uri()

    first = condition_items(document_report(server, uri, request_id=2))
    assert [item["message"] for item in first] == [
        "condition type mismatch: expected 'Bool', got 'Int'"
    ]

    provider.write_text(
        "fn source() -> Bool { return true; }\n",
        encoding="utf-8",
    )
    assert server._sync_closed_workspace_files() is True

    second = condition_items(document_report(server, uri, request_id=3))
    assert second == []


def test_closed_condition_type_stays_conservative_for_ambiguous_call(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.nova"
    second = tmp_path / "second.nova"
    caller = tmp_path / "caller.nova"
    first.write_text("fn source() -> Int { return 1; }\n", encoding="utf-8")
    second.write_text("fn source() -> Int { return 2; }\n", encoding="utf-8")
    caller.write_text("fn main() { if (source()) {} }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = caller.absolute().as_uri()

    report = document_report(server, uri, request_id=2)

    assert condition_items(report) == []
    assert any(
        item.get("code") == "nova.ambiguous-function"
        for item in report["items"]
    )


def test_closed_condition_actions_use_null_versions_and_exact_range(
    tmp_path: Path,
) -> None:
    source = tmp_path / "conditions.nova"
    text = 'fn main() { if (1 + 2) {} while ("value") {} }\n'
    source.write_text(text, encoding="utf-8")
    server = initialized_server(tmp_path, document_changes=True)
    uri = source.absolute().as_uri()
    expression = "1 + 2"
    start = text.index(expression)

    actions = code_actions(
        server,
        uri,
        request_id=2,
        line=0,
        start=start,
        end=start + len(expression),
    )
    selected = {
        action["title"]: action
        for action in actions
        if action["diagnostics"][0].get("code") == "nova.condition-type"
    }

    assert set(selected) == {
        "Compare Int condition with zero",
        "Replace condition with Bool literal",
    }
    typed = selected["Compare Int condition with zero"]
    assert typed["edit"]["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": start},
                        "end": {
                            "line": 0,
                            "character": start + len(expression),
                        },
                    },
                    "newText": "(1 + 2) != 0",
                }
            ],
        }
    ]
    assert all(
        "Compare String condition" not in action["title"]
        for action in actions
    )


def test_closed_condition_action_resolves_lazily(
    tmp_path: Path,
) -> None:
    source = tmp_path / "conditions.nova"
    text = 'fn main() { while ("value") {} }\n'
    source.write_text(text, encoding="utf-8")
    server = initialized_server(
        tmp_path,
        document_changes=True,
        resolve_edit=True,
    )
    uri = source.absolute().as_uri()
    expression = '"value"'
    start = text.index(expression)

    actions = code_actions(
        server,
        uri,
        request_id=2,
        line=0,
        start=start,
        end=start + len(expression),
    )
    typed = next(
        action
        for action in actions
        if action["title"] == "Compare String condition with empty string"
    )
    assert "edit" not in typed

    resolved = server.handle(request("codeAction/resolve", 3, typed))
    assert resolved is not None
    edit = resolved["result"]["edit"]["documentChanges"][0]
    assert edit["textDocument"] == {"uri": uri, "version": None}
    assert edit["edits"][0]["newText"] == '("value") != ""'


def test_closed_condition_layer_is_in_final_runtime_mro() -> None:
    modules = [base.__module__ for base in NovaProductLanguageServer.__mro__]

    assert modules.index("mini_language_server.closed_condition_types") < modules.index(
        "mini_language_server.closed_scalar_diagnostics"
    )
    assert modules.index("mini_language_server.completion_pipeline") < modules.index(
        "mini_language_server.closed_condition_types"
    )
