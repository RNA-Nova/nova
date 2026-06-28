"""
验证迁移后的 Pydantic v2 类型可正常构造、序列化、反序列化。

这些测试不依赖真实网络，只覆盖 harness 层的数据模型。
"""

import pytest
from nova_ai import ThinkingLevel

from nova_harness.core.types.compaction import CompactionSettings
from nova_harness.core.types.messages import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CompactionSummaryMessage,
    CustomMessage,
    FileContent,
)
from nova_harness.core.types.model_registry import (
    CustomModelsResult,
    ModelDefinition,
    ModelsConfig,
    ProviderConfig,
    ProviderOverride,
)
from nova_harness.core.types.session import (
    ActiveToolsChangeEntry,
    BranchSummaryEntry,
    CompactionEntry,
    LeafEntry,
    SessionHeader,
    ThinkingLevelChangeEntry,
)
from nova_harness.core.types.setting import (
    RetrySettings,
    Settings,
)

SIMPLE_TYPES = [
    (
        lambda: FileContent(
            filename="x.py", path="/tmp/x.py", mime_type="text/x-python", size=10
        ),
        FileContent,
    ),
    (
        lambda: BashExecutionMessage(
            command="echo hi",
            output="hi",
            exit_code=0,
            cancelled=False,
            truncated=False,
        ),
        BashExecutionMessage,
    ),
    (
        lambda: CustomMessage(custom_type="test", content="hello", display=True),
        CustomMessage,
    ),
    (
        lambda: BranchSummaryMessage(summary="summary", from_id="b1"),
        BranchSummaryMessage,
    ),
    (
        lambda: CompactionSummaryMessage(summary="summary", tokens_before=100),
        CompactionSummaryMessage,
    ),
    (lambda: CompactionSettings(enabled=True, reserve_tokens=1000), CompactionSettings),
    (lambda: RetrySettings(max_retries=3, max_delay_ms=1000), RetrySettings),
    (
        lambda: ProviderOverride(base_url="https://example.com", api_key="sk"),
        ProviderOverride,
    ),
    (
        lambda: CustomModelsResult(models=[], overrides={}, model_overrides={}),
        CustomModelsResult,
    ),
    (lambda: SessionHeader(id="s1", timestamp="2024-01-01T00:00:00"), SessionHeader),
    (
        lambda: CompactionEntry(
            id="e1",
            summary="compacted",
            first_kept_entry_id="e2",
            tokens_before=200,
            details={"read_files": ["a.py"], "modified_files": []},
        ),
        CompactionEntry,
    ),
    (
        lambda: BranchSummaryEntry(id="e2", summary="branch", from_id="b1"),
        BranchSummaryEntry,
    ),
    (
        lambda: ThinkingLevelChangeEntry(id="e3", thinking_level=ThinkingLevel.MEDIUM),
        ThinkingLevelChangeEntry,
    ),
    (
        lambda: ActiveToolsChangeEntry(id="e4", active_tool_names=["read", "bash"]),
        ActiveToolsChangeEntry,
    ),
    (
        lambda: LeafEntry(id="e5", target_id="e1"),
        LeafEntry,
    ),
]


@pytest.mark.parametrize("factory,cls", SIMPLE_TYPES)
def test_type_roundtrip(factory, cls):
    """每个类型都能 to_dict -> from_dict 无损恢复。"""
    obj = factory()
    data = obj.model_dump()
    restored = cls.model_validate(data)
    assert restored == obj


def test_model_definition_with_compat():
    """ModelDefinition 可携带 nova_ai 的 ModelCost/OpenAICompletionsCompat 并正确序列化。"""
    from nova_ai import ModelCost, OpenAICompletionsCompat

    md = ModelDefinition(
        id="test-model",
        name="Test Model",
        api="openai-completions",
        reasoning=True,
        input=["text"],
        cost=ModelCost(input=1.0, output=2.0),
        context_window=128000,
        max_tokens=4096,
        compat=OpenAICompletionsCompat(thinking_format="deepseek"),
    )
    data = md.model_dump()
    restored = ModelDefinition.model_validate(data)
    assert restored.id == "test-model"
    assert restored.cost.input == 1.0
    assert restored.compat.thinking_format == "deepseek"


def test_settings_with_nested_objects():
    """Settings 嵌套子模型可正常序列化。"""
    settings = Settings(
        default_model="deepseek-v3-2-251201",
        default_thinking_level=ThinkingLevel.HIGH,
        compaction=CompactionSettings(enabled=True),
        retry=RetrySettings(max_retries=5),
    )
    data = settings.model_dump()
    restored = Settings.model_validate(data)
    assert restored.default_model == "deepseek-v3-2-251201"
    assert restored.default_thinking_level == ThinkingLevel.HIGH
    assert restored.compaction.enabled is True
    assert restored.retry.max_retries == 5


def test_settings_enum_serialized_as_string():
    """默认 model_dump 输出中 Enum 字段应为字符串。"""
    settings = Settings(default_thinking_level=ThinkingLevel.LOW)
    data = settings.model_dump()
    assert data["default_thinking_level"] == "low"


def test_models_config_roundtrip():
    """ModelsConfig 整体配置可序列化/反序列化。"""
    config = ModelsConfig(
        providers={
            "volcengine": ProviderConfig(
                base_url="https://ark.cn-beijing.volces.com/api/v3/",
                models=[ModelDefinition(id="deepseek-v3-2-251201", name="DeepSeek")],
            )
        }
    )
    data = config.model_dump()
    restored = ModelsConfig.model_validate(data)
    assert "volcengine" in restored.providers
    assert restored.providers["volcengine"].models[0].id == "deepseek-v3-2-251201"
