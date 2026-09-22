from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialized_server() -> NovaProductLanguageServer:
    server = NovaProductLanguageServer()
    assert server.handle(request("initialize", 1, {"capabilities": {}})) is not None
    return server


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


def diagnostics(server: NovaProductLanguageServer, uri: str, code: str):
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return tuple(item for item in snapshot.diagnostics if item.code == code)


def test_same_file_explicit_never_call_closes_fallthrough_and_suffix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn halt() -> ! { while (true) { continue; } } "
        "fn value() -> Int { halt(); let dead = 1; }\n"
    )
    open_nova(server, uri, text)

    assert diagnostics(server, uri, "nova.missing-return") == ()
    unreachable = diagnostics(server, uri, "nova.unreachable-code")
    assert len(unreachable) == 1
    assert unreachable[0].message == "unreachable code after never-returning call"
    assert text[unreachable[0].span.start : unreachable[0].span.end] == "let dead = 1;"


def test_cross_file_never_call_rebinds_when_annotation_changes() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    helper_never = "fn halt() -> ! { while (true) { continue; } }\n"
    main_text = "fn value() -> Int { halt(); let maybe = 1; }\n"
    open_nova(server, helper_uri, helper_never)
    open_nova(server, main_uri, main_text)

    assert diagnostics(server, main_uri, "nova.missing-return") == ()
    unreachable = diagnostics(server, main_uri, "nova.unreachable-code")
    assert len(unreachable) == 1
    assert main_text[unreachable[0].span.start : unreachable[0].span.end] == (
        "let maybe = 1;"
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn halt() -> Unit { return (); }\n"}
                ],
            },
        )
    )

    assert len(diagnostics(server, main_uri, "nova.missing-return")) == 1
    assert diagnostics(server, main_uri, "nova.unreachable-code") == ()


def test_ambiguous_never_target_remains_conservative() -> None:
    server = initialized_server()
    open_nova(
        server,
        "file:///workspace/left.nova",
        "fn halt() -> ! { while (true) { continue; } }\n",
    )
    open_nova(
        server,
        "file:///workspace/right.nova",
        "fn halt() -> ! { while (true) { continue; } }\n",
    )
    main_uri = "file:///workspace/main.nova"
    open_nova(server, main_uri, "fn value() -> Int { halt(); }\n")

    assert len(diagnostics(server, main_uri, "nova.missing-return")) == 1
    assert diagnostics(server, main_uri, "nova.unreachable-code") == ()
    assert len(diagnostics(server, main_uri, "nova.ambiguous-function")) == 1


def test_bare_return_path_prevents_never_call_from_hiding_missing_return() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn halt() -> ! { while (true) { continue; } } "
            "fn value(flag: Bool) -> Int { "
            "if (flag) { return; } halt(); }\n"
        ),
    )

    assert len(diagnostics(server, uri, "nova.missing-return")) == 1


def test_compound_never_call_is_not_promoted_to_statement_termination() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn halt() -> ! { while (true) { continue; } } "
            "fn consume(value: Int) -> Unit { return (); } "
            "fn value() -> Int { consume(halt()); }\n"
        ),
    )

    assert len(diagnostics(server, uri, "nova.missing-return")) == 1
    assert diagnostics(server, uri, "nova.unreachable-code") == ()

def test_never_call_arm_can_close_value_return_branch_obligation() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn halt() -> ! { while (true) { continue; } } "
            "fn value(flag: Bool) -> Int { "
            "if (flag) { halt(); } else { return 1; } }\n"
        ),
    )

    assert diagnostics(server, uri, "nova.missing-return") == ()


def test_bare_return_arm_remains_invalid_even_when_other_arm_never_returns() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn halt() -> ! { while (true) { continue; } } "
            "fn value(flag: Bool) -> Int { "
            "if (flag) { return; } else { halt(); } }\n"
        ),
    )

    assert len(diagnostics(server, uri, "nova.missing-return")) == 1


def test_nested_never_call_kills_only_its_body_suffix() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn halt() -> ! { while (true) { continue; } } "
        "fn main(flag: Bool) { "
        "if (flag) { halt(); let dead = 1; } let live = 2; }\n"
    )
    open_nova(server, uri, text)

    unreachable = diagnostics(server, uri, "nova.unreachable-code")
    assert len(unreachable) == 1
    assert unreachable[0].message == "unreachable code after never-returning call"
    assert text[unreachable[0].span.start : unreachable[0].span.end] == "let dead = 1;"


def test_cross_file_nested_never_effect_rebinds_after_annotation_change() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(
        server,
        helper_uri,
        "fn halt() -> ! { while (true) { continue; } }\n",
    )
    main_text = (
        "fn value(flag: Bool) -> Int { "
        "if (flag) { halt(); let dead = 1; } else { return 1; } }\n"
    )
    open_nova(server, main_uri, main_text)

    assert diagnostics(server, main_uri, "nova.missing-return") == ()
    unreachable = diagnostics(server, main_uri, "nova.unreachable-code")
    assert len(unreachable) == 1
    assert main_text[unreachable[0].span.start : unreachable[0].span.end] == (
        "let dead = 1;"
    )

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [
                    {"text": "fn halt() -> Unit { return (); }\n"}
                ],
            },
        )
    )

    assert len(diagnostics(server, main_uri, "nova.missing-return")) == 1
    assert diagnostics(server, main_uri, "nova.unreachable-code") == ()

def test_unannotated_divergent_function_infers_never_effect() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn halt() { while (true) { continue; } } "
        "fn value() -> Int { halt(); let dead = 1; }\n"
    )
    open_nova(server, uri, text)

    assert diagnostics(server, uri, "nova.missing-return") == ()
    unreachable = diagnostics(server, uri, "nova.unreachable-code")
    assert len(unreachable) == 1
    assert unreachable[0].message == "unreachable code after never-returning call"
    assert text[unreachable[0].span.start : unreachable[0].span.end] == "let dead = 1;"


def test_unannotated_never_effect_propagates_transitively() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    text = (
        "fn halt() { while (true) { continue; } } "
        "fn wrapper() { halt(); } "
        "fn value() -> Int { wrapper(); let dead = 1; }\n"
    )
    open_nova(server, uri, text)

    assert diagnostics(server, uri, "nova.missing-return") == ()
    unreachable = diagnostics(server, uri, "nova.unreachable-code")
    assert len(unreachable) == 1
    assert text[unreachable[0].span.start : unreachable[0].span.end] == "let dead = 1;"


def test_never_effect_inference_is_cycle_safe() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn left() { right(); } "
            "fn right() { left(); } "
            "fn value() -> Int { left(); }\n"
        ),
    )

    assert len(diagnostics(server, uri, "nova.missing-return")) == 1
    assert diagnostics(server, uri, "nova.unreachable-code") == ()


def test_explicit_non_never_annotation_blocks_effect_inference() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn halt() -> Unit { while (true) { continue; } } "
            "fn value() -> Int { halt(); }\n"
        ),
    )

    assert len(diagnostics(server, uri, "nova.missing-return")) == 1
    assert diagnostics(server, uri, "nova.unreachable-code") == ()


def test_any_syntactic_return_keeps_unannotated_effect_inference_conservative() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_nova(
        server,
        uri,
        (
            "fn maybe(flag: Bool) { "
            "if (flag) { return; } while (true) { continue; } } "
            "fn value() -> Int { maybe(true); }\n"
        ),
    )

    assert len(diagnostics(server, uri, "nova.missing-return")) == 1
    assert diagnostics(server, uri, "nova.unreachable-code") == ()


def test_cross_file_inferred_never_effect_rebinds_transitively() -> None:
    server = initialized_server()
    helper_uri = "file:///workspace/helper.nova"
    wrapper_uri = "file:///workspace/wrapper.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(
        server,
        helper_uri,
        "fn halt() { while (true) { continue; } }\n",
    )
    open_nova(server, wrapper_uri, "fn wrapper() { halt(); }\n")
    main_text = "fn value() -> Int { wrapper(); let dead = 1; }\n"
    open_nova(server, main_uri, main_text)

    assert diagnostics(server, main_uri, "nova.missing-return") == ()
    assert len(diagnostics(server, main_uri, "nova.unreachable-code")) == 1

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": helper_uri, "version": 2},
                "contentChanges": [{"text": "fn halt() { return; }\n"}],
            },
        )
    )

    assert len(diagnostics(server, main_uri, "nova.missing-return")) == 1
    assert diagnostics(server, main_uri, "nova.unreachable-code") == ()
