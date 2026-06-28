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
        self._assert_can_import("nova_ai.types", [
            "NovaBaseModel",
            "TextContent", "ThinkingContent", "ToolCall", "ImageContent",
            "UserMessage", "AssistantMessage", "ToolResultMessage",
            "Tool", "Context", "Usage",
            "TextStartEvent", "TextDeltaEvent", "TextEndEvent",
            "ThinkingStartEvent", "ThinkingDeltaEvent", "ThinkingEndEvent",
            "ToolCallStartEvent", "ToolCallDeltaEvent", "ToolCallEndEvent",
            "DoneEvent", "ErrorEvent",
            "Model", "ModelCost",
            "OpenAICompletionsCompat", "OpenAIResponsesCompat",
            "OpenRouterRouting", "VercelGatewayRouting",
            "KnownApi", "KnownProvider", "ThinkingFormat", "StopReason",
            "SimpleStreamOptions", "StreamOptions",
            "ApiAdapter",
        ])

    def test_registry_imports(self):
        self._assert_can_import("nova_ai.registry", [
            "ApiAdapter", "ModelRegistry",
            "register_api_adapter", "get_api_adapter", "list_api_adapters",
            "has_api_adapter", "unregister_api_adapter", "clear_api_adapters",
            "register_model", "get_model", "get_models_by_provider", "list_providers",
            "list_all_models", "find_model_by_id",
            "register_builtin_api_adapters", "register_builtin_models",
            "register_all_builtins",
            "reset_api_adapter_registry", "reset_model_registry", "reset_registry",
        ])

    def test_utils_env_imports(self):
        self._assert_can_import("nova_ai.utils.env", [
            "get_env_api_key", "get_env_api_key_typed", "get_all_env_api_keys",
        ])

    def test_streaming_imports(self):
        self._assert_can_import("nova_ai.streaming", [
            "stream", "complete", "stream_simple", "complete_simple",
            "AssistantMessageEventStream",
        ])

    def test_providers_imports(self):
        mod = importlib.import_module("nova_ai.api_impls.openai_completions")
        assert hasattr(mod, "build_params")
        assert hasattr(mod, "stream_openai_completions")

    def test_utils_imports(self):
        self._assert_can_import("nova_ai.utils", [
            "get_env_api_key", "get_env_api_key_typed", "get_all_env_api_keys",
            "parse_streaming_json", "sanitize_surrogates",
            "build_base_options", "clamp_reasoning",
            "transform_messages",
            "calculate_cost", "supports_xhigh_thinking", "get_supported_thinking_levels",
            "is_context_overflow",
        ])

    def test_models_imports(self):
        mod = importlib.import_module("nova_ai.models.volcengine")
        assert hasattr(mod, "VOLCENGINE_MODELS")
