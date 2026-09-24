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


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    folder: Path | None = None,
    document_links: bool = True,
) -> dict[str, Any]:
    text_document: dict[str, Any] = {}
    if document_links:
        text_document["documentLink"] = {}
    workspace: dict[str, Any] = {}
    params: dict[str, Any] = {
        "capabilities": {
            "textDocument": text_document,
            "workspace": workspace,
        }
    }
    if folder is not None:
        workspace["workspaceFolders"] = True
        params["workspaceFolders"] = [
            {"uri": folder.as_uri(), "name": "workspace"}
        ]

    response = server.handle(request("initialize", 1, params))
    assert response is not None
    if folder is not None:
        server.handle(notify("initialized", {}))
    return response


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
) -> None:
    server.handle(
        notify(
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


def document_links(
    server: NovaProductLanguageServer,
    uri: str,
    *,
    request_id: int,
) -> dict[str, Any] | None:
    return server.handle(
        request(
            "textDocument/documentLink",
            request_id,
            {"textDocument": {"uri": uri}},
        )
    )


def test_document_link_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    supported_response = initialize(supported)
    assert supported_response["result"]["capabilities"]["documentLinkProvider"] == {
        "resolveProvider": False
    }

    unsupported = NovaProductLanguageServer()
    unsupported_response = initialize(unsupported, document_links=False)
    assert "documentLinkProvider" not in unsupported_response["result"]["capabilities"]

    uri = "file:///workspace/main.nova"
    open_nova(unsupported, uri, "fn main() {}\n")
    assert document_links(unsupported, uri, request_id=2) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_document_link_targets_exact_closed_workspace_snapshot(tmp_path: Path) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n")
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path)

    importer_uri = (tmp_path / "main.nova").as_uri()
    source = "import ./dep.nova;\r\nfn main() {}\r\n"
    open_nova(server, importer_uri, source)

    response = document_links(server, importer_uri, request_id=10)

    assert response == {
        "jsonrpc": "2.0",
        "id": 10,
        "result": [
            {
                "range": {
                    "start": {"line": 0, "character": 7},
                    "end": {"line": 0, "character": 17},
                },
                "target": target.as_uri(),
            }
        ],
    }


def test_document_link_resolves_workspace_root_import(tmp_path: Path) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n")
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path)

    importer_uri = (tmp_path / "main.nova").as_uri()
    source = "import @/dep.nova;\nfn main() {}\n"
    open_nova(server, importer_uri, source)

    assert document_links(server, importer_uri, request_id=101) == {
        "jsonrpc": "2.0",
        "id": 101,
        "result": [
            {
                "range": {
                    "start": {"line": 0, "character": 7},
                    "end": {"line": 0, "character": 17},
                },
                "target": target.as_uri(),
            }
        ],
    }


def test_document_link_omits_unresolved_imports(tmp_path: Path) -> None:
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path)
    uri = (tmp_path / "main.nova").as_uri()
    open_nova(
        server,
        uri,
        "import ./missing.nova;\nfn main() {}\n",
    )

    assert document_links(server, uri, request_id=11) == {
        "jsonrpc": "2.0",
        "id": 11,
        "result": [],
    }


def test_document_link_requires_open_document(tmp_path: Path) -> None:
    source = tmp_path / "main.nova"
    target = tmp_path / "dep.nova"
    source.write_text("import ./dep.nova;\nfn main() {}\n")
    target.write_text("fn dep() {}\n")
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path)

    assert document_links(server, source.as_uri(), request_id=12) == {
        "jsonrpc": "2.0",
        "id": 12,
        "result": [],
    }


def test_document_link_rejects_workspace_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n")
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path)
    uri = (tmp_path / "main.nova").as_uri()
    open_nova(server, uri, "import ./dep.nova;\nfn main() {}\n")

    original = server.workspace_symbols.commit_snapshots_if_current
    drifted = False

    def drift_then_commit(snapshots, commit):
        nonlocal drifted
        if not drifted:
            drifted = True
            other_uri = (tmp_path / "other.nova").as_uri()
            open_nova(server, other_uri, "fn other() {}\n")
        return original(snapshots, commit)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        drift_then_commit,
    )

    assert document_links(server, uri, request_id=13) == {
        "jsonrpc": "2.0",
        "id": 13,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_document_link_honors_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n")
    server = NovaProductLanguageServer()
    initialize(server, folder=tmp_path)
    uri = (tmp_path / "main.nova").as_uri()
    open_nova(server, uri, "import ./dep.nova;\nfn main() {}\n")
    original = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context) -> None:
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        original(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_then_checkpoint)

    assert document_links(server, uri, request_id=14) == {
        "jsonrpc": "2.0",
        "id": 14,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
