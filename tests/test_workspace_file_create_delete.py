from __future__ import annotations

from pathlib import Path

from mini_language_server import NovaProductLanguageServer
from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize(
    server: WorkspaceNovaLanguageServer,
    root: Path,
    *,
    did_create: bool = False,
    did_delete: bool = False,
) -> dict:
    file_operations: dict[str, bool] = {}
    if did_create:
        file_operations["didCreate"] = True
    if did_delete:
        file_operations["didDelete"] = True
    workspace: dict = {
        "workspaceFolders": True,
        "symbol": {},
    }
    if file_operations:
        workspace["fileOperations"] = file_operations
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {"workspace": workspace},
                "workspaceFolders": [{"uri": root.as_uri(), "name": "workspace"}],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))
    return response


def open_nova(
    server: WorkspaceNovaLanguageServer,
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


def diagnostic_codes(server: WorkspaceNovaLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    ]


def test_create_delete_capabilities_are_negotiated_independently(
    tmp_path: Path,
) -> None:
    create_server = WorkspaceNovaLanguageServer()
    create = initialize(create_server, tmp_path, did_create=True)
    create_ops = create["result"]["capabilities"]["workspace"]["fileOperations"]
    assert create_ops["didCreate"] == {
        "filters": [{"scheme": "file", "pattern": {"glob": "**/*.nova"}}]
    }
    assert "didDelete" not in create_ops
    assert "didRename" not in create_ops

    delete_server = WorkspaceNovaLanguageServer()
    delete = initialize(delete_server, tmp_path, did_delete=True)
    delete_ops = delete["result"]["capabilities"]["workspace"]["fileOperations"]
    assert delete_ops["didDelete"] == {
        "filters": [{"scheme": "file", "pattern": {"glob": "**/*.nova"}}]
    }
    assert "didCreate" not in delete_ops
    assert "didRename" not in delete_ops


def test_create_and_delete_rebind_open_cross_file_diagnostics_and_navigation(
    tmp_path: Path,
) -> None:
    server = NovaProductLanguageServer()
    initialize(server, tmp_path, did_create=True, did_delete=True)

    caller_path = tmp_path / "main.nova"
    caller_uri = caller_path.absolute().as_uri()
    caller_text = "fn main() { target() }\n"
    open_nova(server, caller_uri, caller_text)
    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)

    provider = tmp_path / "provider.nova"
    provider.write_bytes(b"fn target() {}\n")
    provider_uri = provider.absolute().as_uri()
    server.handle(
        notify(
            "workspace/didCreateFiles",
            {"files": [{"uri": provider_uri}]},
        )
    )

    assert server.documents.get(provider_uri) is None
    assert server.workspace_symbols.get(provider_uri) is not None
    assert "nova.unresolved-function" not in diagnostic_codes(server, caller_uri)
    definition = server.handle(
        request(
            "textDocument/definition",
            2,
            {
                "textDocument": {"uri": caller_uri},
                "position": {"line": 0, "character": caller_text.index("target") + 1},
            },
        )
    )
    assert definition is not None
    assert definition["result"]["uri"] == provider_uri

    provider.unlink()
    server.handle(
        notify(
            "workspace/didDeleteFiles",
            {"files": [{"uri": provider_uri}]},
        )
    )

    assert server.workspace_symbols.get(provider_uri) is None
    assert "nova.unresolved-function" in diagnostic_codes(server, caller_uri)


def test_delete_notification_does_not_displace_equivalent_open_buffer(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "lib~.nova"
    provider.write_bytes(b"fn target() {}\n")
    server = NovaProductLanguageServer()
    initialize(server, tmp_path, did_delete=True)

    disk_uri = provider.absolute().as_uri()
    open_uri = disk_uri.replace("~", "%7E")
    open_nova(server, open_uri, "fn target(flag: Bool) { flag }\n", version=4)
    assert server.workspace_symbols.get(open_uri) is server.semantics.get(open_uri)

    provider.unlink()
    server.handle(
        notify(
            "workspace/didDeleteFiles",
            {"files": [{"uri": disk_uri}]},
        )
    )

    assert server.workspace_symbols.get(open_uri) is server.semantics.get(open_uri)
    declarations = server.workspace_symbols.declarations("target")
    assert [(item.uri, item.snapshot.symbols.syntax.document.text) for item in declarations] == [
        (open_uri, "fn target(flag: Bool) { flag }\n")
    ]

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": open_uri}})
    )
    assert server.workspace_symbols.declarations("target") == ()


def test_create_delete_ignore_out_of_scope_and_non_nova_resources(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    server = WorkspaceNovaLanguageServer()
    initialize(server, root, did_create=True, did_delete=True)

    outside_file = outside / "outside.nova"
    outside_file.write_bytes(b"fn outside() {}\n")
    text_file = root / "ignored.txt"
    text_file.write_bytes(b"fn ignored() {}\n")
    server.handle(
        notify(
            "workspace/didCreateFiles",
            {
                "files": [
                    {"uri": outside_file.absolute().as_uri()},
                    {"uri": text_file.absolute().as_uri()},
                    {"uri": "https://example.com/remote.nova"},
                ]
            },
        )
    )

    assert server.workspace_symbols.search("") == ()


def test_malformed_create_batch_is_rejected_before_any_index_mutation(
    tmp_path: Path,
) -> None:
    provider = tmp_path / "provider.nova"
    provider.write_bytes(b"fn target() {}\n")
    server = WorkspaceNovaLanguageServer()
    initialize(server, tmp_path, did_create=True, did_delete=True)

    # Remove the initial detached contribution so this notification is the only
    # possible source of a new workspace mutation.
    provider_uri = provider.absolute().as_uri()
    provider.unlink()
    server.handle(
        notify("workspace/didDeleteFiles", {"files": [{"uri": provider_uri}]})
    )
    provider.write_bytes(b"fn target() {}\n")
    before = server.workspace_symbols.snapshots()

    server.handle(
        notify(
            "workspace/didCreateFiles",
            {"files": [{"uri": provider_uri}, {"uri": 42}]},
        )
    )

    after = server.workspace_symbols.snapshots()
    assert after.generation == before.generation
    assert server.workspace_symbols.get(provider_uri) is None
