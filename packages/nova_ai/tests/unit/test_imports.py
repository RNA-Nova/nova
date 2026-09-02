"""
导入测试 - 验证所有公共 API 可导入
"""

import importlib


class TestRootImports:
    """根包导入测试"""

    def _assert_can_import(self, module_name: str, names: list) -> None:
        mod = importlib.import_module(module_name)
        for name in names:
            assert hasattr(mod, name), f"{module_name}.{name} 不可导入"

    def test_types_imports(self):
        self._assert_can_import(
            "nova_ai.types",
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
                "SimpleStreamOptions",
                "StreamOptions",
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
                "Models",
                "create_models",
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
                "sanitize_surrogates",
                "build_base_options",
                "transform_messages",
                "calculate_cost",
                "clamp_thinking_level",
                "get_supported_thinking_levels",
                "to_thinking_level",
                "is_context_overflow",
            ],
        )

    def test_providers_imports(self):
        mod = importlib.import_module("nova_ai.providers.volcengine")
        assert hasattr(mod, "VOLCENGINE_MODELS")
