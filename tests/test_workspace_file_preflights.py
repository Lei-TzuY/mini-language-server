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


def initialized_server(
    root: Path,
    *,
    will_create: bool = False,
    will_delete: bool = False,
    did_create: bool = False,
    did_delete: bool = False,
) -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    file_operations: dict[str, bool] = {}
    if will_create:
        file_operations["willCreate"] = True
    if will_delete:
        file_operations["willDelete"] = True
    if did_create:
        file_operations["didCreate"] = True
    if did_delete:
        file_operations["didDelete"] = True

    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "workspaceFolders": True,
                        "fileOperations": file_operations,
                    }
                },
                "workspaceFolders": [
                    {"uri": root.as_uri(), "name": "workspace"},
                ],
            },
        )
    )
    assert response is not None
    operations = response["result"]["capabilities"]["workspace"]["fileOperations"]
    expected = {
        name
        for name, supported in (
            ("willCreate", will_create),
            ("willDelete", will_delete),
            ("didCreate", did_create),
            ("didDelete", did_delete),
        )
        if supported
    }
    assert set(operations) == expected
    server.handle(notify("initialized", {}))
    return server


def file_preflight(
    server: NovaProductLanguageServer,
    method: str,
    *uris: str,
    request_id: int,
):
    return server.handle(
        request(
            method,
            request_id,
            {"files": [{"uri": uri} for uri in uris]},
        )
    )


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


def test_will_create_and_delete_are_negotiated_independently(tmp_path: Path) -> None:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "fileOperations": {
                            "willCreate": True,
                            "willDelete": False,
                            "didCreate": False,
                            "didDelete": True,
                        }
                    }
                }
            },
        )
    )
    assert response is not None
    operations = response["result"]["capabilities"]["workspace"]["fileOperations"]
    assert set(operations) == {"willCreate", "didDelete"}


def test_will_create_preflights_new_scoped_nova_without_mutation(
    tmp_path: Path,
) -> None:
    server = initialized_server(tmp_path, will_create=True)
    target = tmp_path / "new.nova"
    uri = target.as_uri()
    before = server.workspace_symbols.snapshots()

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        uri,
        request_id=10,
    )

    assert response == {"jsonrpc": "2.0", "id": 10, "result": None}
    assert not target.exists()
    after = server.workspace_symbols.snapshots()
    assert after.generation == before.generation
    assert tuple(after) == tuple(before)


def test_will_create_rejects_canonical_duplicate_targets(tmp_path: Path) -> None:
    server = initialized_server(tmp_path, will_create=True)
    plain = (tmp_path / "lib~.nova").as_uri()
    equivalent = plain.replace("~", "%7E")

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        plain,
        equivalent,
        request_id=11,
    )

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "create identities must be unique" in response["error"]["message"]


def test_will_create_rejects_open_target_collision(tmp_path: Path) -> None:
    server = initialized_server(tmp_path, will_create=True)
    uri = (tmp_path / "open.nova").as_uri()
    open_nova(server, uri, "fn open_target() {}\n")

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        uri,
        request_id=12,
    )

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "create target already open" in response["error"]["message"]
    assert server.documents.get(uri) is not None


def test_will_create_rejects_detached_target_collision(tmp_path: Path) -> None:
    target = tmp_path / "closed.nova"
    target.write_text("fn closed_target() {}\n", encoding="utf-8")
    server = initialized_server(tmp_path, will_create=True)
    uri = target.as_uri()
    before = server.workspace_symbols.get(uri)
    assert before is not None

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        uri,
        request_id=13,
    )

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "create target already indexed" in response["error"]["message"]
    assert server.workspace_symbols.get(uri) is before
    assert target.exists()


def test_will_delete_preflights_open_source_without_mutation(tmp_path: Path) -> None:
    server = initialized_server(tmp_path, will_delete=True)
    uri = (tmp_path / "open.nova").as_uri()
    open_nova(server, uri, "fn open_target() {}\n", version=4)
    before = server.documents.get(uri)
    assert before is not None

    response = file_preflight(
        server,
        "workspace/willDeleteFiles",
        uri,
        request_id=14,
    )

    assert response == {"jsonrpc": "2.0", "id": 14, "result": None}
    assert server.documents.get(uri) is before
    assert before.version == 4


def test_will_delete_preflights_detached_source_without_mutation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn closed_target() {}\n", encoding="utf-8")
    server = initialized_server(tmp_path, will_delete=True)
    uri = source.as_uri()
    before = server.workspace_symbols.get(uri)
    assert before is not None

    response = file_preflight(
        server,
        "workspace/willDeleteFiles",
        uri,
        request_id=15,
    )

    assert response == {"jsonrpc": "2.0", "id": 15, "result": None}
    assert source.exists()
    assert server.workspace_symbols.get(uri) is before


def test_will_file_preflight_rejects_malformed_params(tmp_path: Path) -> None:
    server = initialized_server(
        tmp_path,
        will_create=True,
        will_delete=True,
    )

    for request_id, method in (
        (16, "workspace/willCreateFiles"),
        (17, "workspace/willDeleteFiles"),
    ):
        response = server.handle(
            request(method, request_id, {"files": [{"missing": "uri"}]})
        )
        assert response == {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32602, "message": "Invalid params"},
        }


def test_will_create_honors_cancellation_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = initialized_server(tmp_path, will_create=True)
    uri = (tmp_path / "new.nova").as_uri()
    original = server.documents.commit_matching_if_current

    def cancel_then_commit(documents, include, commit):
        assert server.requests.cancel(18) is True
        return original(documents, include, commit)

    monkeypatch.setattr(
        server.documents,
        "commit_matching_if_current",
        cancel_then_commit,
    )

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        uri,
        request_id=18,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 18,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    assert not (tmp_path / "new.nova").exists()


def test_will_delete_rejects_open_document_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = initialized_server(tmp_path, will_delete=True)
    uri = (tmp_path / "open.nova").as_uri()
    open_nova(server, uri, "fn before() {}\n", version=1)
    original = server.documents.commit_matching_if_current

    def replace_then_commit(documents, include, commit):
        server.documents.replace(
            uri=uri,
            version=2,
            text="fn after() {}\n",
        )
        return original(documents, include, commit)

    monkeypatch.setattr(
        server.documents,
        "commit_matching_if_current",
        replace_then_commit,
    )

    response = file_preflight(
        server,
        "workspace/willDeleteFiles",
        uri,
        request_id=19,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 19,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_will_delete_rejects_workspace_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_text("fn closed_target() {}\n", encoding="utf-8")
    server = initialized_server(tmp_path, will_delete=True)
    uri = source.as_uri()
    original = server.workspace_symbols.commit_snapshots_if_current
    drifted = False

    def drift_then_commit(snapshots, callback):
        nonlocal drifted
        if not drifted:
            drifted = True
            other_uri = (tmp_path / "other.nova").as_uri()
            open_nova(server, other_uri, "fn other() {}\n")
        return original(snapshots, callback)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        drift_then_commit,
    )

    response = file_preflight(
        server,
        "workspace/willDeleteFiles",
        uri,
        request_id=20,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 20,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_will_create_rejects_folder_generation_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = initialized_server(tmp_path, will_create=True)
    uri = (tmp_path / "new.nova").as_uri()
    original = server.workspace_folders.commit_if_current
    changed = False

    def change_then_commit(generation, callback):
        nonlocal changed
        if not changed:
            changed = True
            server.workspace_folders.apply_change(
                {
                    "event": {
                        "added": [],
                        "removed": [
                            {"uri": tmp_path.as_uri(), "name": "workspace"},
                        ],
                    }
                }
            )
        return original(generation, callback)

    monkeypatch.setattr(
        server.workspace_folders,
        "commit_if_current",
        change_then_commit,
    )

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        uri,
        request_id=21,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 21,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_will_delete_rejects_detached_disk_drift_and_refreshes_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "closed.nova"
    source.write_bytes(b"fn before() {}\n")
    server = initialized_server(tmp_path, will_delete=True)
    uri = source.as_uri()
    original = server.workspace_symbols.commit_snapshots_if_current
    drifted = False

    def drift_then_commit(snapshots, callback):
        nonlocal drifted
        if not drifted:
            drifted = True
            source.write_bytes(b"fn after() {}\n")
        return original(snapshots, callback)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        drift_then_commit,
    )

    response = file_preflight(
        server,
        "workspace/willDeleteFiles",
        uri,
        request_id=22,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 22,
        "error": {"code": -32801, "message": "Content modified"},
    }
    refreshed = server.workspace_symbols.get(uri)
    assert refreshed is not None
    assert refreshed.symbols.syntax.document.text == "fn after() {}\n"

def test_will_create_rejects_unindexed_local_disk_collision(tmp_path: Path) -> None:
    target = tmp_path / "external.nova"
    target.write_bytes(b"\xff")
    server = initialized_server(tmp_path, will_create=True)
    uri = target.as_uri()
    assert server.workspace_symbols.get(uri) is None

    response = file_preflight(
        server,
        "workspace/willCreateFiles",
        uri,
        request_id=23,
    )

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "create target already exists on disk" in response["error"]["message"]
    assert target.read_bytes() == b"\xff"
    assert server.workspace_symbols.get(uri) is None


def test_will_delete_rejects_unindexed_local_path_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "external.nova"
    source.write_bytes(b"\xff")
    server = initialized_server(tmp_path, will_delete=True)
    uri = source.as_uri()
    assert server.workspace_symbols.get(uri) is None
    original = server.workspace_symbols.commit_snapshots_if_current
    removed = False

    def remove_then_commit(snapshots, callback):
        nonlocal removed
        if not removed:
            removed = True
            source.unlink()
        return original(snapshots, callback)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        remove_then_commit,
    )

    response = file_preflight(
        server,
        "workspace/willDeleteFiles",
        uri,
        request_id=24,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 24,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert not source.exists()
