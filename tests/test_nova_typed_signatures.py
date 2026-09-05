from __future__ import annotations

from typing import Any

from mini_language_server.nova import NovaFunctionAdapter, NovaLanguageServer


def request(
    method: str, request_id: int, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaLanguageServer) -> None:
    response = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert response is not None and "result" in response


def open_nova(server: NovaLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": text,
                }
            },
        )
    )


def test_adapter_accepts_real_typed_nova_function_signatures() -> None:
    text = (
        "fn helper(value: Int) -> Int { value }\n"
        "fn main() -> Int { let local = 1 helper(local) }\n"
    )
    parsed = NovaFunctionAdapter.parse(text)

    assert [name for name, _ in parsed.declarations] == ["helper", "main"]
    assert [parameter.name for parameter in parsed.parameters] == ["value"]
    assert [reference.name for reference in parsed.parameter_references] == ["value"]
    assert [local.name for local in parsed.locals] == ["local"]
    assert [reference.name for reference in parsed.local_references] == ["local"]
    assert [name for name, _ in parsed.calls] == ["helper"]
    assert not parsed.unresolved_names


def test_typed_nova_signature_publishes_parameter_and_unresolved_name_semantics() -> None:
    server = NovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/typed-signature.nova"
    open_nova(server, uri, "fn main(input: Int) -> Int { input missing }\n")

    semantics = server.semantics.get(uri)
    diagnostics = server.diagnostics.get(uri)
    assert semantics is not None
    assert diagnostics is not None
    assert [(symbol.name, symbol.kind) for symbol in semantics.symbols.symbols] == [
        ("main", "function"),
        ("input", "parameter"),
    ]
    assert len(semantics.references) == 1
    assert semantics.references[0].target.name == "input"
    assert semantics.references[0].target.kind == "parameter"
    assert [item.code for item in diagnostics.diagnostics] == ["nova.unresolved-name"]
    assert diagnostics.diagnostics[0].message == "unresolved name 'missing'"


def test_legacy_bounded_untyped_signature_remains_supported() -> None:
    parsed = NovaFunctionAdapter.parse("fn main(input) { input }\n")
    assert [parameter.name for parameter in parsed.parameters] == ["input"]
    assert [reference.name for reference in parsed.parameter_references] == ["input"]
