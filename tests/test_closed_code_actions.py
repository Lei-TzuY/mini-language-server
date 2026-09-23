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
    only: list[str] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {"diagnostics": []}
    if only is not None:
        context["only"] = only
    return {
        "textDocument": {"uri": uri},
        "range": {
            "start": {"line": 0, "character": 12},
            "end": {"line": 0, "character": 19},
        },
        "context": context,
    }


def closed_action(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    request_id: int = 2,
    only: list[str] | None = None,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/codeAction",
            request_id,
            code_action_params(uri, only=only),
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
