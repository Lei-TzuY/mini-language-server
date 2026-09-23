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
    document_changes: bool = False,
    change_annotations: bool = False,
    resolve_edit: bool = False,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    code_action: dict[str, Any] = {}
    if resolve_edit:
        code_action["resolveSupport"] = {"properties": ["edit"]}

    workspace_edit: dict[str, Any] = {}
    if document_changes:
        workspace_edit["documentChanges"] = True
    if change_annotations:
        workspace_edit["changeAnnotationSupport"] = {}

    workspace: dict[str, Any] = {"workspaceFolders": True}
    if workspace_edit:
        workspace["workspaceEdit"] = workspace_edit

    initialized = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {"codeAction": code_action},
                    "workspace": workspace,
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"},
                ],
            },
        )
    )
    assert initialized is not None
    server.handle(notify("initialized", {}))
    return server


def code_action_params(
    uri: str,
    *,
    line: int = 0,
    start: int = 12,
    end: int = 19,
    only: list[str] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {"diagnostics": []}
    if only is not None:
        context["only"] = only
    return {
        "textDocument": {"uri": uri},
        "range": {
            "start": {"line": line, "character": start},
            "end": {"line": line, "character": end},
        },
        "context": context,
    }


def closed_action(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    request_id: int = 2,
    line: int = 0,
    start: int = 12,
    end: int = 19,
    only: list[str] | None = None,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            code_action_params(
                uri,
                line=line,
                start=start,
                end=end,
                only=only,
            ),
        )
    )
    assert response is not None
    return response


def test_closed_unresolved_function_gets_shared_quick_fix(tmp_path: Path) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    response = closed_action(server, uri)

    assert len(response["result"]) == 1
    action = response["result"][0]
    assert action["title"] == "Create function 'missing'"
    assert action["kind"] == "quickfix"
    assert action["diagnostics"][0]["code"] == "nova.unresolved-function"
    assert action["edit"] == {
        "changes": {
            uri: [
                {
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 0},
                    },
                    "newText": "fn missing() {}\n",
                }
            ]
        }
    }
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_quick_fix_uses_null_version_and_annotation_when_negotiated(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(
        tmp_path,
        document_changes=True,
        change_annotations=True,
    )
    uri = source.absolute().as_uri()

    action = closed_action(server, uri)["result"][0]
    edit = action["edit"]

    assert "changes" not in edit
    assert edit["changeAnnotations"] == {
        "edit:1": {"label": "Create function 'missing'"}
    }
    assert edit["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 1, "character": 0},
                        "end": {"line": 1, "character": 0},
                    },
                    "newText": "fn missing() {}\n",
                    "annotationId": "edit:1",
                }
            ],
        }
    ]


def test_closed_quick_fix_resolves_lazily_when_negotiated(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(
        tmp_path,
        document_changes=True,
        resolve_edit=True,
    )
    uri = source.absolute().as_uri()

    action = closed_action(server, uri)["result"][0]

    assert "edit" not in action
    assert action["data"]["novaCodeActionResolve"] >= 1

    resolved = server.handle(request("codeAction/resolve", 3, action))
    assert resolved is not None
    assert resolved["result"]["edit"]["documentChanges"][0]["textDocument"] == {
        "uri": uri,
        "version": None,
    }


def test_closed_code_action_resolve_rejects_disk_drift_and_refreshes_index(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(
        tmp_path,
        document_changes=True,
        resolve_edit=True,
    )
    uri = source.absolute().as_uri()
    action = closed_action(server, uri)["result"][0]
    replacement = "fn missing() {}\nfn main() { missing() }\n"
    source.write_bytes(replacement.encode("utf-8"))

    assert server.handle(request("codeAction/resolve", 3, action)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }
    refreshed = server.workspace_symbols.get(uri)
    assert refreshed is not None
    assert refreshed.symbols.syntax.document.text == replacement


def test_closed_code_action_resolve_rejects_workspace_generation_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(tmp_path, resolve_edit=True)
    uri = source.absolute().as_uri()
    action = closed_action(server, uri)["result"][0]

    provider = tmp_path / "provider.nova"
    provider.write_bytes(b"fn missing() {}\n")
    assert server._sync_closed_workspace_files() is True

    assert server.handle(request("codeAction/resolve", 3, action)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_closed_code_action_resolve_rejects_open_buffer_takeover(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.nova"
    text = "fn main() { missing() }\n"
    source.write_text(text, encoding="utf-8")
    server = initialized_server(tmp_path, resolve_edit=True)
    uri = source.absolute().as_uri()
    action = closed_action(server, uri)["result"][0]

    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 9,
                    "text": text,
                }
            },
        )
    )

    assert server.handle(request("codeAction/resolve", 3, action)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_closed_code_action_resolve_honors_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(tmp_path, resolve_edit=True)
    uri = source.absolute().as_uri()
    action = closed_action(server, uri)["result"][0]
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(
        server.requests,
        "checkpoint",
        cancel_before_checkpoint,
    )

    assert server.handle(request("codeAction/resolve", 3, action)) == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32800, "message": "Request cancelled"},
    }


def test_closed_quick_fix_rejects_disk_drift_and_refreshes_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()
    original = server._nova_unresolved_function_actions
    replacement = "fn missing() {}\nfn main() { missing() }\n"

    def drift_after_plan(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        actions = original(*args, **kwargs)
        source.write_bytes(replacement.encode("utf-8"))
        return actions

    monkeypatch.setattr(
        server,
        "_nova_unresolved_function_actions",
        drift_after_plan,
    )

    assert closed_action(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
    refreshed = server.workspace_symbols.get(uri)
    assert refreshed is not None
    assert refreshed.symbols.syntax.document.text == replacement


def test_closed_quick_fix_rejects_workspace_generation_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()
    original = server._nova_unresolved_function_actions
    provider = tmp_path / "provider.nova"

    def mutate_workspace(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        actions = original(*args, **kwargs)
        provider.write_text("fn missing() {}\n", encoding="utf-8")
        assert server._sync_closed_workspace_files() is True
        return actions

    monkeypatch.setattr(
        server,
        "_nova_unresolved_function_actions",
        mutate_workspace,
    )

    assert closed_action(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_open_buffer_takes_over_closed_quick_fix_version_ownership(
    tmp_path: Path,
) -> None:
    source = tmp_path / "main.nova"
    text = "fn main() { missing() }\n"
    source.write_text(text, encoding="utf-8")
    server = initialized_server(tmp_path, document_changes=True)
    uri = source.absolute().as_uri()

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

    action = closed_action(server, uri)["result"][0]

    assert action["edit"]["documentChanges"][0]["textDocument"] == {
        "uri": uri,
        "version": 7,
    }


def test_closed_quick_fix_respects_only_filter(tmp_path: Path) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    assert closed_action(server, uri, only=["refactor"])["result"] == []


def test_closed_quick_fix_honors_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "main.nova"
    source.write_text("fn main() { missing() }\n", encoding="utf-8")
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

    assert closed_action(server, uri, request_id=8) == {
        "jsonrpc": "2.0",
        "id": 8,
        "error": {"code": -32800, "message": "Request cancelled"},
    }

def test_closed_lazy_action_rejects_open_takeover_during_planning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "main.nova"
    text = "fn main() { missing() }\n"
    source.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path, resolve_edit=True)
    uri = source.absolute().as_uri()
    original = server._nova_unresolved_function_actions

    def open_after_plan(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        actions = original(*args, **kwargs)
        server.handle(
            notify(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": "nova",
                        "version": 11,
                        "text": text,
                    }
                },
            )
        )
        return actions

    monkeypatch.setattr(
        server,
        "_nova_unresolved_function_actions",
        open_after_plan,
    )

    assert closed_action(server, uri, request_id=20) == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert server.documents.get(uri) is not None

def test_closed_return_type_mismatch_gets_literal_repair(tmp_path: Path) -> None:
    source = tmp_path / "return.nova"
    text = 'fn value() -> Int { return "bad"; }\n'
    source.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()
    start = text.index('"bad"')

    response = closed_action(
        server,
        uri,
        request_id=30,
        start=start,
        end=start + len('"bad"'),
    )

    assert len(response["result"]) == 1
    action = response["result"][0]
    assert action["title"] == "Replace return expression with Int literal"
    assert action["diagnostics"][0]["code"] == "nova.return-type"
    assert action["edit"]["changes"][uri] == [
        {
            "range": {
                "start": {"line": 0, "character": start},
                "end": {"line": 0, "character": start + len('"bad"')},
            },
            "newText": "0",
        }
    ]
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_local_type_mismatch_gets_literal_repair_with_null_version(
    tmp_path: Path,
) -> None:
    source = tmp_path / "local.nova"
    text = 'fn caller() { let local: Int = "bad"; }\n'
    source.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path, document_changes=True)
    uri = source.absolute().as_uri()
    start = text.index('"bad"')

    response = closed_action(
        server,
        uri,
        request_id=31,
        start=start,
        end=start + len('"bad"'),
    )

    assert len(response["result"]) == 1
    action = response["result"][0]
    assert action["title"] == "Replace local initializer with Int literal"
    assert action["diagnostics"][0]["code"] == "nova.local-type"
    assert action["edit"]["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": start},
                        "end": {"line": 0, "character": start + len('"bad"')},
                    },
                    "newText": "0",
                }
            ],
        }
    ]


def test_closed_type_repairs_respect_requested_range(tmp_path: Path) -> None:
    source = tmp_path / "types.nova"
    first = 'fn value() -> Int { return "bad"; }'
    second = 'fn caller() { let local: String = 1; }'
    text = f"{first}\n{second}\n"
    source.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path)
    uri = source.absolute().as_uri()

    return_start = first.index('"bad"')
    returned = closed_action(
        server,
        uri,
        request_id=32,
        line=0,
        start=return_start,
        end=return_start + len('"bad"'),
    )
    assert [item["title"] for item in returned["result"]] == [
        "Replace return expression with Int literal"
    ]

    local_start = second.index("1")
    local = closed_action(
        server,
        uri,
        request_id=33,
        line=1,
        start=local_start,
        end=local_start + 1,
    )
    assert [item["title"] for item in local["result"]] == [
        "Replace local initializer with String literal"
    ]
    assert local["result"][0]["edit"]["changes"][uri][0]["newText"] == '""'


def test_closed_type_mismatch_repair_uses_lazy_resolve(tmp_path: Path) -> None:
    source = tmp_path / "return.nova"
    text = 'fn value() -> Bool { return 1; }\n'
    source.write_bytes(text.encode("utf-8"))
    server = initialized_server(
        tmp_path,
        document_changes=True,
        resolve_edit=True,
    )
    uri = source.absolute().as_uri()
    start = text.index("1")

    action = closed_action(
        server,
        uri,
        request_id=34,
        start=start,
        end=start + 1,
    )["result"][0]

    assert action["title"] == "Replace return expression with Bool literal"
    assert "edit" not in action
    assert action["data"]["novaCodeActionResolve"] >= 1

    resolved = server.handle(request("codeAction/resolve", 35, action))
    assert resolved is not None
    assert resolved["result"]["edit"]["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": start},
                        "end": {"line": 0, "character": start + 1},
                    },
                    "newText": "false",
                }
            ],
        }
    ]

def test_closed_argument_count_gets_captured_call_repair(tmp_path: Path) -> None:
    source = tmp_path / "main.nova"
    text = (
        "fn target(left: Int, right: Int) {}\n"
        "fn caller(value: Int) { target(value) }\n"
    )
    source.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path, document_changes=True)
    uri = source.absolute().as_uri()
    call_start = text.splitlines()[1].index("target")

    action = closed_action(
        server,
        uri,
        request_id=40,
        line=1,
        start=call_start,
        end=call_start + len("target"),
    )["result"][0]

    assert action["title"] == "Adjust 'target' to 2 argument(s)"
    assert action["diagnostics"][0]["code"] == "nova.argument-count"
    assert action["edit"]["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {
                            "line": 1,
                            "character": call_start + len("target("),
                        },
                        "end": {
                            "line": 1,
                            "character": call_start + len("target(value"),
                        },
                    },
                    "newText": "value, 0",
                }
            ],
        }
    ]
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_argument_count_uses_cross_file_provider_signature(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_bytes(b"fn target(left: Int, right: Int, third: Int) {}\n")
    text = "fn caller(value: Int) { target(value) }\n"
    caller.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path)
    uri = caller.absolute().as_uri()
    start = text.index("target")

    action = closed_action(
        server,
        uri,
        request_id=41,
        start=start,
        end=start + len("target"),
    )["result"][0]

    assert action["title"] == "Adjust 'target' to 3 argument(s)"
    assert action["edit"]["changes"][uri][0]["newText"] == "value, 0, 0"


def test_closed_argument_type_uses_cross_file_provider_type(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    caller = tmp_path / "caller.nova"
    provider.write_bytes(b"fn target(value: String) {}\n")
    text = "fn caller() { target(1) }\n"
    caller.write_bytes(text.encode("utf-8"))
    server = initialized_server(tmp_path, document_changes=True)
    uri = caller.absolute().as_uri()
    literal = text.index("1")

    action = closed_action(
        server,
        uri,
        request_id=42,
        start=literal,
        end=literal + 1,
    )["result"][0]

    assert action["title"] == "Replace argument 1 to 'target' with String literal"
    assert action["diagnostics"][0]["code"] == "nova.argument-type"
    assert action["edit"]["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": literal},
                        "end": {"line": 0, "character": literal + 1},
                    },
                    "newText": '""',
                }
            ],
        }
    ]
    assert server.documents.get(uri) is None
    assert server.diagnostics.get(uri) is None


def test_closed_call_site_repairs_respect_range_and_lazy_resolve(
    tmp_path: Path,
) -> None:
    source = tmp_path / "calls.nova"
    first = "fn pair(left: Int, right: Int) {}"
    second = "fn text(value: String) {}"
    third = "fn caller(value: Int) { pair(value) text(1) }"
    source.write_bytes(f"{first}\n{second}\n{third}\n".encode("utf-8"))
    server = initialized_server(
        tmp_path,
        document_changes=True,
        resolve_edit=True,
    )
    uri = source.absolute().as_uri()

    count_start = third.index("pair")
    count_action = closed_action(
        server,
        uri,
        request_id=43,
        line=2,
        start=count_start,
        end=count_start + len("pair"),
    )["result"]
    assert [item["title"] for item in count_action] == [
        "Adjust 'pair' to 2 argument(s)"
    ]

    literal = third.index("1")
    type_actions = closed_action(
        server,
        uri,
        request_id=44,
        line=2,
        start=literal,
        end=literal + 1,
    )["result"]
    assert [item["title"] for item in type_actions] == [
        "Replace argument 1 to 'text' with String literal"
    ]
    action = type_actions[0]
    assert "edit" not in action
    resolved = server.handle(request("codeAction/resolve", 45, action))
    assert resolved is not None
    assert resolved["result"]["edit"]["documentChanges"] == [
        {
            "textDocument": {"uri": uri, "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 2, "character": literal},
                        "end": {"line": 2, "character": literal + 1},
                    },
                    "newText": '""',
                }
            ],
        }
    ]
