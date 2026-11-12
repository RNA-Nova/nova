"""
导入测试 - 验证所有公共 API 可导入
"""

import importlib
import subprocess
import sys


class TestRootImports:
    """根包导入测试"""

    def _assert_can_import(self, module_name: str, names: list) -> None:
        mod = importlib.import_module(module_name)
        for name in names:
            assert hasattr(mod, name), f"{module_name}.{name} 不可导入"

    def test_types_imports(self):
        self._assert_can_import(
            "nova_protocol",
            [
                "NovaBaseModel",
                "TextContent",
                "ThinkingContent",
                "ToolCall",
                "ImageContent",
                "UserMessage",
                "AssistantMessage",
                "ToolResultMessage",
                "Tool",
                "Context",
                "Usage",
                "TextStartEvent",
                "TextDeltaEvent",
                "TextEndEvent",
                "ThinkingStartEvent",
                "ThinkingDeltaEvent",
                "ThinkingEndEvent",
                "ToolCallStartEvent",
                "ToolCallDeltaEvent",
                "ToolCallEndEvent",
                "DoneEvent",
                "ErrorEvent",
                "Model",
                "ModelCost",
                "OpenAICompletionsCompat",
                "OpenAIResponsesCompat",
                "OpenRouterRouting",
                "VercelGatewayRouting",
                "KnownApi",
                "KnownProvider",
                "ThinkingFormat",
                "StopReason",
            ],
        )

    def test_stream_options_imports(self):
        self._assert_can_import(
            "nova_ai.stream_options",
            [
                "SimpleStreamOptions",
                "StreamOptions",
                "ThinkingBudgets",
                "ProviderResponse",
            ],
        )

    def test_utils_env_imports(self):
        self._assert_can_import(
            "nova_ai.utils.env",
            [
                "get_env_api_key",
            ],
        )

    def test_streaming_imports(self):
        self._assert_can_import(
            "nova_ai.streaming",
            [
                "AssistantMessageEventStream",
                "EventStream",
                "create_assistant_message_event_stream",
            ],
        )

    def test_models_imports(self):
        self._assert_can_import(
            "nova_ai",
            [
                "ModelsManager",
                "create_models_manager",
                "builtin_models",
                "get_builtin_model",
                "get_builtin_models",
            ],
        )

    def test_providers_imports(self):
        mod = importlib.import_module("nova_ai.api_impls.openai_completions")
        assert hasattr(mod, "build_params")
        assert hasattr(mod, "stream")

    def test_utils_imports(self):
        self._assert_can_import(
            "nova_ai.utils",
            [
                "get_env_api_key",
                "parse_streaming_json",
                "StreamingJsonParser",
                "sanitize_surrogates",
                "calculate_cost",
                "clamp_thinking_level",
                "get_supported_thinking_levels",
                "to_thinking_level",
                "is_context_overflow",
            ],
        )

    def test_api_impls_shared_imports(self):
        """实现层共享件的家：消息转换 / streamSimple 选项（utils 去混层后）。"""
        self._assert_can_import(
            "nova_ai.api_impls._shared",
            [
                "build_base_options",
                "transform_messages",
                "clamp_max_tokens_to_context",
                "resolve_cache_retention",
            ],
        )

    def test_volcengine_imports(self):
        mod = importlib.import_module("nova_ai.providers.volcengine")
        assert hasattr(mod, "VOLCENGINE_MODELS")


def test_oauth_flow_modules_are_not_eagerly_imported():
    code = (
        "import sys; import nova_ai; "
        "assert 'kimi_oauth' in dir(nova_ai); "
        "assert 'openai_codex_oauth' in dir(nova_ai); "
        "assert 'nova_ai.auth.oauth.kimi' not in sys.modules; "
        "assert 'nova_ai.auth.oauth.openai_codex' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_image_public_surface_imports():
    import nova_protocol

    import nova_ai

    for name in ("ImageModel", "ImagesContext", "AssistantImages", "ImagesStopReason"):
        assert hasattr(nova_protocol, name)
    for name in ("ImagesOptions", "ProviderImages", "get_builtin_image_models"):
        assert hasattr(nova_ai, name)
    from nova_ai.api_impls import openrouter_images

    assert hasattr(openrouter_images, "generate_images")


def test_api_impls_dir_includes_lazy_option_union():
    import nova_ai.api_impls as api_impls

    assert "ProviderStreamOptions" in dir(api_impls)


def test_classifier_public_surface_imports():
    import nova_protocol

    import nova_ai

    for name in (
        "ClassifierModel",
        "ClassifierContext",
        "ClassifierResult",
        "ClassifierStopReason",
    ):
        assert hasattr(nova_protocol, name)
    for name in (
        "ClassifierOptions",
        "ProviderClassifier",
        "get_builtin_classifier_models",
        "OPENROUTER_CLASSIFIER_MODELS",
    ):
        assert hasattr(nova_ai, name)
    from nova_ai.api_impls import typesafe_system_one

    assert hasattr(typesafe_system_one, "classify")
