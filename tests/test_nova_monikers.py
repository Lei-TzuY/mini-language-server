from __future__ import annotations

from threading import Event, Thread
from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(
    method: str,
    request_id: int,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    *,
    moniker: bool = True,
    workspace_folders: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    text_document: dict[str, Any] = {}
    if moniker:
        text_document["moniker"] = {}
    capabilities: dict[str, Any] = {
        "textDocument": text_document,
        "workspace": {"workspaceFolders": True},
    }
    params: dict[str, Any] = {"capabilities": capabilities}
    if workspace_folders is not None:
        params["workspaceFolders"] = workspace_folders
    response = server.handle(request("initialize", 1, params))
    assert response is not None
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


def moniker_at(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    needle: str,
    request_id: int,
    *,
    occurrence: int = 0,
) -> dict[str, Any]:
    offset = -1
    start = 0
    for _ in range(occurrence + 1):
        offset = text.index(needle, start)
        start = offset + len(needle)
    position = server._source_text(text).position_at(offset)
    response = server.handle(
        request(
            "textDocument/moniker",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {
                    "line": position.line,
                    "character": position.character,
                },
            },
        )
    )
    assert response is not None
    return response


def configured_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = initialize(
        server,
        workspace_folders=[
            {"uri": "file:///workspace", "name": "workspace"},
        ],
    )
    assert response["result"]["capabilities"]["monikerProvider"] is True
    return server


def test_moniker_is_advertised_only_when_client_supports_it() -> None:
    supported = configured_server()
    assert supported._moniker_support is True

    unsupported = NovaProductLanguageServer()
    response = initialize(
        unsupported,
        moniker=False,
        workspace_folders=[
            {"uri": "file:///workspace", "name": "workspace"},
        ],
    )
    assert "monikerProvider" not in response["result"]["capabilities"]


def test_unique_workspace_function_has_project_local_moniker() -> None:
    server = configured_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn target() -> Unit { return (); }\n"
        "fn main() -> Unit { target(); }\n"
    )
    open_nova(server, uri, text)

    response = moniker_at(server, uri, text, "target", 2, occurrence=1)

    assert response["result"] == [
        {
            "scheme": "nova",
            "identifier": "target",
            "unique": "project",
            "kind": "local",
        }
    ]


def test_cross_file_call_reuses_function_project_moniker() -> None:
    server = configured_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper = "fn target() -> Unit { return (); }\n"
    main = "fn main() -> Unit { target(); }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, main_uri, main)

    declaration = moniker_at(server, helper_uri, helper, "target", 2)
    call = moniker_at(server, main_uri, main, "target", 3)

    assert declaration["result"] == call["result"]
    assert call["result"][0]["identifier"] == "target"


def test_ambiguous_function_name_has_no_moniker() -> None:
    server = configured_server()
    open_nova(
        server,
        "file:///workspace/a.nova",
        "fn target() -> Unit { return (); }\n",
    )
    open_nova(
        server,
        "file:///workspace/b.nova",
        "fn target() -> Unit { return (); }\n",
    )
    uri = "file:///workspace/main.nova"
    text = "fn main() -> Unit { target(); }\n"
    open_nova(server, uri, text)

    response = moniker_at(server, uri, text, "target", 2)

    assert response["result"] == []


def test_unscoped_session_does_not_claim_project_uniqueness() -> None:
    server = NovaProductLanguageServer()
    initialize(server, workspace_folders=None)
    uri = "file:///workspace/main.nova"
    text = "fn target() -> Unit { return (); }\n"
    open_nova(server, uri, text)

    response = moniker_at(server, uri, text, "target", 2)

    assert response["result"] == []


def test_out_of_scope_function_does_not_get_project_moniker() -> None:
    server = configured_server()
    uri = "file:///outside/main.nova"
    text = "fn target() -> Unit { return (); }\n"
    open_nova(server, uri, text)

    response = moniker_at(server, uri, text, "target", 2)

    assert response["result"] == []


def test_workspace_folder_change_invalidates_inflight_moniker(monkeypatch) -> None:
    server = configured_server()
    uri = "file:///workspace/main.nova"
    text = "fn target() -> Unit { return (); }\n"
    open_nova(server, uri, text)

    entered = Event()
    release = Event()
    original = server._project_function_moniker

    def blocked(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(timeout=5)
        return result

    monkeypatch.setattr(server, "_project_function_moniker", blocked)
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            moniker_at(server, uri, text, "target", 2)
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [],
                    "removed": [
                        {"uri": "file:///workspace", "name": "workspace"}
                    ],
                }
            },
        )
    )
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32801, "message": "Content modified"},
        }
    ]


def test_moniker_request_is_cancellable(monkeypatch) -> None:
    server = configured_server()
    uri = "file:///workspace/main.nova"
    text = "fn target() -> Unit { return (); }\n"
    open_nova(server, uri, text)

    entered = Event()
    release = Event()
    original = server._project_function_moniker

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(server, "_project_function_moniker", blocked)
    responses: list[dict[str, Any] | None] = []
    thread = Thread(
        target=lambda: responses.append(
            moniker_at(server, uri, text, "target", 2)
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    server.handle(notify("$/cancelRequest", {"id": 2}))
    release.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32800, "message": "Request cancelled"},
        }
    ]
    assert len(server.requests) == 0
