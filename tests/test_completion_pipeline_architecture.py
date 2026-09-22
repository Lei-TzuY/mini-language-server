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

def test_unary_plus_diagnostic_and_action_share_one_runtime_layer() -> None:
    modules = {base.__module__ for base in NovaProductLanguageServer.__mro__}

    assert "mini_language_server.unary_plus" in modules
    assert modules.isdisjoint(
        {
            "mini_language_server.unary_plus_diagnostics",
            "mini_language_server.unary_plus_actions",
        }
    )

def test_uint_type_semantics_share_one_runtime_layer() -> None:
    modules = {base.__module__ for base in NovaProductLanguageServer.__mro__}

    assert "mini_language_server.uint_types" in modules
    assert modules.isdisjoint(
        {
            "mini_language_server.uint_intrinsic_types",
            "mini_language_server.uint_numeric_types",
            "mini_language_server.uint_conversion_types",
        }
    )


def test_uint_conversion_diagnostic_and_action_share_one_runtime_layer() -> None:
    modules = {base.__module__ for base in NovaProductLanguageServer.__mro__}

    assert "mini_language_server.uint_conversion" in modules
    assert modules.isdisjoint(
        {
            "mini_language_server.uint_conversion_diagnostics",
            "mini_language_server.uint_conversion_actions",
        }
    )

def test_expression_typing_capabilities_share_one_runtime_product_layer() -> None:
    modules = {base.__module__ for base in NovaProductLanguageServer.__mro__}

    assert "mini_language_server.expression_types" in modules
    assert modules.isdisjoint(
        {
            "mini_language_server.arithmetic_expression_types",
            "mini_language_server.comparison_expression_types",
            "mini_language_server.logical_expression_types",
        }
    )
