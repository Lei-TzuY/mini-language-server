from mini_language_server import NovaProductLanguageServer


def test_completion_capabilities_share_one_runtime_product_layer() -> None:
    modules = {base.__module__ for base in NovaProductLanguageServer.__mro__}

    assert "mini_language_server.completion_pipeline" in modules
    assert modules.isdisjoint(
        {
            "mini_language_server.function_completion_snippets",
            "mini_language_server.completion_insert_replace",
            "mini_language_server.completion_classification",
            "mini_language_server.completion_prefix",
        }
    )
