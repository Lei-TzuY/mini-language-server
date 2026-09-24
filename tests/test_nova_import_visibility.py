from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": {
                        "callHierarchy": {},
                        "codeLens": {},
                        "definition": {"linkSupport": True},
                        "signatureHelp": {},
                    }
                }
            },
        )
    )
    assert response is not None
    return server


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
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


def open_namespace_fixture(
    server: NovaProductLanguageServer,
) -> dict[str, tuple[str, str]]:
    files = {
        "helper": ("file:///workspace/helper.nova", "fn helper() {}\n"),
        "a": (
            "file:///workspace/a.nova",
            (
                "import ./helper.nova;\n"
                "fn target(value: Int) -> Int { value }\n"
                "fn only_a() {}\n"
            ),
        ),
        "b": (
            "file:///workspace/b.nova",
            (
                "fn target(value: String) -> String { value }\n"
                "fn only_b() {}\n"
            ),
        ),
        "caller_a": (
            "file:///workspace/caller-a.nova",
            "import ./a.nova;\nfn caller_a() { target(1) }\n",
        ),
        "caller_b": (
            "file:///workspace/caller-b.nova",
            'import ./b.nova;\nfn caller_b() { target("x") }\n',
        ),
        "legacy": (
            "file:///workspace/legacy.nova",
            "fn legacy() { target(1) }\n",
        ),
    }
    for uri, text in files.values():
        open_nova(server, uri, text)
    return files


def position(text: str, marker: str, *, delta: int = 0) -> dict[str, int]:
    offset = text.index(marker) + delta
    line = text.count("\n", 0, offset)
    line_start = text.rfind("\n", 0, offset) + 1
    return {"line": line, "character": offset - line_start}


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    ]


def test_direct_import_visibility_aligns_editor_resolution_surfaces() -> None:
    server = initialized_server()
    files = open_namespace_fixture(server)
    a_uri, _ = files["a"]
    caller_uri, caller = files["caller_a"]
    legacy_uri, legacy = files["legacy"]
    call = position(caller, "target", delta=1)

    assert "nova.ambiguous-function" not in diagnostic_codes(server, caller_uri)
    assert "nova.unresolved-function" not in diagnostic_codes(server, caller_uri)
    assert "nova.ambiguous-function" in diagnostic_codes(server, legacy_uri)

    definition = server.handle(
        request(
            "textDocument/definition",
            10,
            {"textDocument": {"uri": caller_uri}, "position": call},
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == a_uri
    assert definition["result"][0]["targetSelectionRange"]["start"]["line"] == 1
    assert definition["result"][0]["targetRange"]["end"]["line"] == 1

    hover = server.handle(
        request(
            "textDocument/hover",
            11,
            {"textDocument": {"uri": caller_uri}, "position": call},
        )
    )
    assert hover is not None
    assert hover["result"]["contents"]["value"] == "fn target(value: Int) -> Int"

    signature = server.handle(
        request(
            "textDocument/signatureHelp",
            12,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "1"),
            },
        )
    )
    assert signature is not None
    assert signature["result"]["signatures"][0]["label"] == (
        "fn target(value: Int) -> Int"
    )

    completion = server.handle(
        request(
            "textDocument/completion",
            13,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "target"),
            },
        )
    )
    assert completion is not None
    labels = {item["label"] for item in completion["result"]}
    assert "only_a" in labels
    assert "only_b" not in labels

    legacy_definition = server.handle(
        request(
            "textDocument/definition",
            14,
            {
                "textDocument": {"uri": legacy_uri},
                "position": position(legacy, "target", delta=1),
            },
        )
    )
    assert legacy_definition is not None
    assert legacy_definition["result"] is None


def test_direct_import_visibility_bounds_references_hierarchy_and_rename() -> None:
    server = initialized_server()
    files = open_namespace_fixture(server)
    a_uri, a = files["a"]
    b_uri, _ = files["b"]
    caller_a_uri, caller_a = files["caller_a"]
    caller_b_uri, _ = files["caller_b"]
    legacy_uri, _ = files["legacy"]
    call = position(caller_a, "target", delta=1)

    references = server.handle(
        request(
            "textDocument/references",
            20,
            {
                "textDocument": {"uri": caller_a_uri},
                "position": call,
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["uri"] for item in references["result"]] == [
        a_uri,
        caller_a_uri,
    ]

    prepared = server.handle(
        request(
            "textDocument/prepareCallHierarchy",
            21,
            {"textDocument": {"uri": caller_a_uri}, "position": call},
        )
    )
    assert prepared is not None
    assert len(prepared["result"]) == 1
    target = prepared["result"][0]
    assert target["uri"] == a_uri

    incoming = server.handle(
        request("callHierarchy/incomingCalls", 22, {"item": target})
    )
    assert incoming is not None
    assert [item["from"]["uri"] for item in incoming["result"]] == [caller_a_uri]

    caller_item = server.handle(
        request(
            "textDocument/prepareCallHierarchy",
            23,
            {
                "textDocument": {"uri": caller_a_uri},
                "position": position(caller_a, "caller_a", delta=1),
            },
        )
    )
    assert caller_item is not None
    outgoing = server.handle(
        request(
            "callHierarchy/outgoingCalls",
            24,
            {"item": caller_item["result"][0]},
        )
    )
    assert outgoing is not None
    assert [item["to"]["uri"] for item in outgoing["result"]] == [a_uri]

    rename = server.handle(
        request(
            "textDocument/rename",
            25,
            {
                "textDocument": {"uri": caller_a_uri},
                "position": call,
                "newName": "target_a",
            },
        )
    )
    assert rename is not None
    changes = rename["result"]["changes"]
    assert set(changes) == {a_uri, caller_a_uri}
    assert all(
        edit["newText"] == "target_a"
        for edits in changes.values()
        for edit in edits
    )
    assert b_uri not in changes
    assert caller_b_uri not in changes
    assert legacy_uri not in changes

    declaration_line = a[: a.index("fn target")].count("\n")
    assert changes[a_uri][0]["range"]["start"]["line"] == declaration_line


def test_direct_import_visibility_scopes_reference_code_lens() -> None:
    server = initialized_server()
    files = open_namespace_fixture(server)
    a_uri, _ = files["a"]
    caller_a_uri, _ = files["caller_a"]

    lenses = server.handle(
        request(
            "textDocument/codeLens",
            30,
            {"textDocument": {"uri": a_uri}},
        )
    )
    assert lenses is not None
    target_lens = next(
        item
        for item in lenses["result"]
        if item.get("data", {}).get("name") == "target"
    )

    resolved = server.handle(request("codeLens/resolve", 31, target_lens))
    assert resolved is not None
    assert resolved["result"]["command"]["title"] == "1 reference"

    locations = server.handle(
        request(
            "workspace/executeCommand",
            32,
            {
                "command": "mini-language-server.showReferences",
                "arguments": [{"uri": a_uri, "name": "target"}],
            },
        )
    )
    assert locations is not None
    assert [item["uri"] for item in locations["result"]] == [caller_a_uri]

def test_transitive_import_visibility_aligns_resolution_surfaces() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    middle_uri = "file:///workspace/middle.nova"
    caller_uri = "file:///workspace/caller.nova"
    helper = "fn transit(value: Int) -> Int { value }\n"
    middle = "import ./helper.nova;\nfn middle() {}\n"
    caller = "import ./middle.nova;\nfn caller() { transit(1) }\n"
    open_nova(server, helper_uri, helper)
    open_nova(server, middle_uri, middle)
    open_nova(server, caller_uri, caller)
    call = position(caller, "transit", delta=1)

    assert "nova.unresolved-function" not in diagnostic_codes(server, caller_uri)
    assert "nova.ambiguous-function" not in diagnostic_codes(server, caller_uri)

    definition = server.handle(
        request(
            "textDocument/definition",
            40,
            {"textDocument": {"uri": caller_uri}, "position": call},
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == helper_uri

    hover = server.handle(
        request(
            "textDocument/hover",
            41,
            {"textDocument": {"uri": caller_uri}, "position": call},
        )
    )
    assert hover is not None
    assert hover["result"]["contents"]["value"] == "fn transit(value: Int) -> Int"

    completion = server.handle(
        request(
            "textDocument/completion",
            42,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "transit"),
            },
        )
    )
    assert completion is not None
    assert "transit" in {item["label"] for item in completion["result"]}

    references = server.handle(
        request(
            "textDocument/references",
            43,
            {
                "textDocument": {"uri": caller_uri},
                "position": call,
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    assert [item["uri"] for item in references["result"]] == [
        helper_uri,
        caller_uri,
    ]

    rename = server.handle(
        request(
            "textDocument/rename",
            44,
            {
                "textDocument": {"uri": caller_uri},
                "position": call,
                "newName": "transit_renamed",
            },
        )
    )
    assert rename is not None
    assert set(rename["result"]["changes"]) == {helper_uri, caller_uri}


def test_transitive_import_visibility_deduplicates_diamonds_and_cycles() -> None:
    server = initialized_server()
    shared_uri = "file:///workspace/shared.nova"
    left_uri = "file:///workspace/left.nova"
    right_uri = "file:///workspace/right.nova"
    root_uri = "file:///workspace/root.nova"
    cycle_a_uri = "file:///workspace/cycle-a.nova"
    cycle_b_uri = "file:///workspace/cycle-b.nova"

    open_nova(server, shared_uri, "fn shared() {}\n")
    open_nova(
        server,
        left_uri,
        "import ./shared.nova;\nfn left() {}\n",
    )
    open_nova(
        server,
        right_uri,
        "import ./shared.nova;\nfn right() {}\n",
    )
    root = (
        "import ./left.nova;\n"
        "import ./right.nova;\n"
        "fn root() { shared() }\n"
    )
    open_nova(server, root_uri, root)

    assert "nova.ambiguous-function" not in diagnostic_codes(server, root_uri)
    definition = server.handle(
        request(
            "textDocument/definition",
            50,
            {
                "textDocument": {"uri": root_uri},
                "position": position(root, "shared", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == shared_uri

    open_nova(
        server,
        cycle_a_uri,
        "import ./cycle-b.nova;\nfn from_a() {}\n",
    )
    cycle_b = "import ./cycle-a.nova;\nfn from_b() { from_a() }\n"
    open_nova(server, cycle_b_uri, cycle_b)

    assert "nova.unresolved-function" not in diagnostic_codes(server, cycle_b_uri)
    cycle_definition = server.handle(
        request(
            "textDocument/definition",
            51,
            {
                "textDocument": {"uri": cycle_b_uri},
                "position": position(cycle_b, "from_a", delta=1),
            },
        )
    )
    assert cycle_definition is not None
    assert cycle_definition["result"][0]["targetUri"] == cycle_a_uri


def test_transitive_import_visibility_preserves_local_precedence() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider.nova"
    middle_uri = "file:///workspace/middle-local.nova"
    caller_uri = "file:///workspace/caller-local.nova"
    open_nova(server, provider_uri, "fn target(value: String) -> String { value }\n")
    open_nova(
        server,
        middle_uri,
        "import ./provider.nova;\nfn middle() {}\n",
    )
    caller = (
        "import ./middle-local.nova;\n"
        "fn target(value: Int) -> Int { value }\n"
        "fn caller() { target(1) }\n"
    )
    open_nova(server, caller_uri, caller)

    call = position(caller, "target(1)", delta=1)
    definition = server.handle(
        request(
            "textDocument/definition",
            60,
            {"textDocument": {"uri": caller_uri}, "position": call},
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == caller_uri
    assert definition["result"][0]["targetSelectionRange"]["start"]["line"] == 1
    assert "nova.ambiguous-function" not in diagnostic_codes(server, caller_uri)


def test_transitive_import_visibility_preserves_intermediate_local_shadowing() -> None:
    server = initialized_server()
    provider_uri = "file:///workspace/provider-shadow.nova"
    middle_uri = "file:///workspace/middle-shadow.nova"
    caller_uri = "file:///workspace/caller-shadow.nova"
    open_nova(server, provider_uri, "fn target(value: String) -> String { value }\n")
    middle = (
        "import ./provider-shadow.nova;\n"
        "fn target(value: Int) -> Int { value }\n"
    )
    open_nova(server, middle_uri, middle)
    caller = (
        "import ./middle-shadow.nova;\n"
        "fn caller() { target(1) }\n"
    )
    open_nova(server, caller_uri, caller)

    assert "nova.ambiguous-function" not in diagnostic_codes(server, caller_uri)
    assert "nova.argument-type" not in diagnostic_codes(server, caller_uri)
    definition = server.handle(
        request(
            "textDocument/definition",
            70,
            {
                "textDocument": {"uri": caller_uri},
                "position": position(caller, "target", delta=1),
            },
        )
    )
    assert definition is not None
    assert definition["result"][0]["targetUri"] == middle_uri
    assert definition["result"][0]["targetSelectionRange"]["start"]["line"] == 1
