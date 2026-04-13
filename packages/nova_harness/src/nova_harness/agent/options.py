# ============================================================================
# 配置类定义
# ============================================================================
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pi_agent import Agent, AgentTool
from nova_ai import (
    ImageContent, Model, ThinkingLevel,
)
from ..model_registry import ModelRegistry
from ..session import SessionManager
from ..setting import SettingsManager
from ..computex import ComputexManager
from ..resource import ResourceLoader
from mashumaro.mixins.json import DataClassJSONMixin

@dataclass
class ScopedModelConfig:
    model: Model
    thinking_level: ThinkingLevel


@dataclass
class SessionTokens:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


@dataclass
class AgentSessionConfig:
    agent: 'Agent'
    system_prompt_fn: callable
    session_manager: 'SessionManager'
    settings_manager: 'SettingsManager'
    computex_manager: 'ComputexManager'
    cwd: str
    scoped_models: List[ScopedModelConfig] = field(default_factory=list)
    resource_loader: Optional['ResourceLoader'] = None
    model_registry: Optional['ModelRegistry'] = None
    initial_active_tool_names: List[str] = field(
        default_factory=lambda: ["read", "bash", "edit", "write"]
    )
    base_tools_override: Optional[Dict[str, 'AgentTool']] = None

    def __post_init__(self):
        if not self.cwd:
            raise ValueError("cwd (current working directory) cannot be empty")
        if self.agent is None:
            raise ValueError("agent cannot be None")


@dataclass
class PromptOptions:
    expand_prompt_templates: bool = True
    images: List['ImageContent'] = field(default_factory=list)
    streaming_behavior: Optional[str] = None

    def __post_init__(self):
        if self.streaming_behavior is not None:
            valid_behaviors = {"steer", "follow_up"}
            if self.streaming_behavior not in valid_behaviors:
                raise ValueError(
                    f"streaming_behavior must be one of {valid_behaviors}, "
                    f"got {self.streaming_behavior}"
                )


@dataclass
class ModelCycleResult:
    model: Model
    thinking_level: ThinkingLevel
    is_scoped: bool


@dataclass
class SessionStats:
    session_id: str
    session_file: Optional[str] = None
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    total_messages: int = 0
    tokens: SessionTokens = field(default_factory=SessionTokens)
    cost: float = 0.0

    def __post_init__(self):
        self.total_messages = (
            self.user_messages + self.assistant_messages +
            self.tool_calls + self.tool_results
        )

@dataclass
class NavigateOptions(DataClassJSONMixin):
    """
    会话树导航选项
    
    Attributes:
        summarize: 是否生成从当前位置到目标位置的分支摘要
        label: 为目标节点添加标签（仅在 summarize=False 时直接添加到目标节点）
        custom_instructions: 生成摘要时的自定义指令
        replace_instructions: 替换默认的摘要生成指令
        reserve_tokens: 为摘要预留的 token 数量（覆盖默认设置）
    """
    summarize: bool = False
    label: Optional[str] = None
    custom_instructions: Optional[str] = None
    replace_instructions: Optional[str] = None
    reserve_tokens: Optional[int] = None