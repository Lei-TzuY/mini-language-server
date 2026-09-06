from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int = 1, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize(server: NovaProductLanguageServer, *, supported: bool = True) -> dict:
    text_document = {"diagnostic": {}} if supported else {}
    response = server.handle(
        request("initialize", params={"capabilities": {"textDocument": text_document}})
    )
    assert response is not None
    return response


def open_document(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    version: int = 1,
) -> None:
    server.handle(
        notification(
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


def pull_diagnostics(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int = 2,
    previous_result_id: str | None = None,
) -> dict:
    params: dict[str, Any] = {"textDocument": {"uri": uri}}
    if previous_result_id is not None:
        params["previousResultId"] = previous_result_id
    response = server.handle(
        request("textDocument/diagnostic", request_id=request_id, params=params)
    )
    assert response is not None
    return response


def test_pull_diagnostic_capability_is_negotiated() -> None:
    supported = NovaProductLanguageServer()
    provider = initialize(supported)["result"]["capabilities"]["diagnosticProvider"]
    assert provider == {
        "interFileDependencies": False,
        "workspaceDiagnostics": False,
    }

    unsupported = NovaProductLanguageServer()
    response = initialize(unsupported, supported=False)
    assert "diagnosticProvider" not in response["result"]["capabilities"]


def test_pull_diagnostics_return_full_then_unchanged_report() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")

    first = pull_diagnostics(server, uri)
    report = first["result"]
    assert report["kind"] == "full"
    assert report["resultId"].startswith("1:")
    assert [(item["code"], item["message"]) for item in report["items"]] == [
        ("nova.unresolved-function", "unresolved function 'missing'")
    ]

    second = pull_diagnostics(
        server,
        uri,
        request_id=3,
        previous_result_id=report["resultId"],
    )
    assert second["result"] == {
        "kind": "unchanged",
        "resultId": report["resultId"],
    }


def test_pull_diagnostics_follow_change_and_close_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    old_result_id = pull_diagnostics(server, uri)["result"]["resultId"]

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {"text": "fn missing() {}\nfn main() {\n  missing()\n}\n"}
                ],
            },
        )
    )
    changed = pull_diagnostics(
        server,
        uri,
        request_id=3,
        previous_result_id=old_result_id,
    )["result"]
    assert changed["kind"] == "full"
    assert changed["resultId"].startswith("2:")
    assert changed["items"] == []

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))
    open_document(server, uri, "fn main() {\n  other_missing()\n}\n", version=1)
    reopened = pull_diagnostics(
        server,
        uri,
        request_id=4,
        previous_result_id=old_result_id,
    )["result"]
    assert reopened["kind"] == "full"
    assert reopened["resultId"] != old_result_id
    assert reopened["items"][0]["code"] == "nova.unresolved-function"


def test_pull_diagnostics_reject_same_version_snapshot_replacement(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    original_checkpoint = server.requests.checkpoint
    calls = 0

    def replace_on_second_checkpoint(context: Any) -> None:
        nonlocal calls
        calls += 1
        original_checkpoint(context)
        if calls == 2:
            document = server.documents.get(uri)
            assert document is not None
            server.nova_adapter.publish(server, document)

    monkeypatch.setattr(server.requests, "checkpoint", replace_on_second_checkpoint)
    assert pull_diagnostics(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_pull_diagnostics_honor_cancellation(monkeypatch: Any) -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "fn main() {\n  missing()\n}\n")
    original_checkpoint = server.requests.checkpoint

    def cancel_before_checkpoint(context: Any) -> None:
        server.requests.cancel(context.request_id)
        original_checkpoint(context)

    monkeypatch.setattr(server.requests, "checkpoint", cancel_before_checkpoint)
    assert pull_diagnostics(server, uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
