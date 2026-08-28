"""RPC 方法线上形状声明（契约的方法表）。

每个方法在此声明所属域、参数模型与结果模型；三方共用同一份声明：

1. **分派校验**：``MethodRegistry.dispatch`` 在调用处理器前用 params 模型
   校验/规范化（缺参/类型错误 → ``INVALID_PARAMS``）；
2. **schema 导出**：``schema_export`` 把本表汇总为 ``methods`` 根，
   供其他语言的后端/前端 codegen；
3. **能力位宣告**：``initialize`` 按域聚合返回。

自由负载（``Any`` / ``dict`` 字段）如实标注为 unknown——不虚构结构；
结果形状确实自由的方法（navigateTree/fork/getContextUsage/pkg*）不声明
结果模型，schema 中记为 ``{}``。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from nova_ai import ImageContent
from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field, RootModel, SerializeAsAny, StrictBool

from nova_harness.core.types.resources.selection import CapabilitySelection
from nova_harness.core.types.resources.tools import ToolInfo
from nova_harness.server.types.items import NovaItem, WireItem

# ---------------------------------------------------------------------------
# 公共小模型
# ---------------------------------------------------------------------------


class OkResult(NovaBaseModel):
    success: bool


class ModelRef(NovaBaseModel):
    provider: str
    id: str


# ---------------------------------------------------------------------------
# session 域
# ---------------------------------------------------------------------------


class EmptyParams(NovaBaseModel):
    """无参数方法（params 可省略或为空对象）。"""


class CapabilitiesInfo(NovaBaseModel):
    domains: List[str]
    methods: List[str]


class InitializeResult(NovaBaseModel):
    version: str
    contract_version_major: int
    contract_version_minor: int
    capabilities: CapabilitiesInfo


class CreateSessionParams(NovaBaseModel):
    cwd: Optional[str] = None
    model: Optional[Union[str, Dict[str, Any]]] = None
    thinking_level: Optional[str] = None
    agent_name: Optional[str] = None
    session_flag: Optional[str] = None
    continue_last: StrictBool = False
    agent_dir: Optional[str] = None
    # 显式会话文件（pi --session <file|id> 启动恢复）：绝对路径直用，
    # 裸 id 在 cwd 的默认会话目录解析 <id>.jsonl；与 session_flag /
    # continue_last 互斥（同时给出报参数错误）
    session_file: Optional[str] = None
    # 临时会话（pi --no-session 对位）：内存态运行、不落盘不进会话列表；
    # 与 session_flag / continue_last / session_file 互斥（恢复与临时语义矛盾）
    no_session: StrictBool = False


class CreateSessionResult(NovaBaseModel):
    session_id: str
    session_name: Optional[str] = None
    resumed: bool = False


class NewSessionResult(NovaBaseModel):
    session_id: str
    session_name: Optional[str] = None


class SwitchSessionParams(NovaBaseModel):
    path: Optional[str] = None
    session_id: Optional[str] = None
    cwd: Optional[str] = None


class SwitchSessionResult(NovaBaseModel):
    success: bool
    cancelled: Optional[bool] = None
    session_id: Optional[str] = None
    session_name: Optional[str] = None


class ListSessionsParams(NovaBaseModel):
    cwd: Optional[str] = None
    # 作用域：current=按 cwd 的默认会话目录；all=全局 sessions 根下所有项目目录
    scope: Literal["current", "all"] = "current"


class SessionListItem(NovaBaseModel):
    """会话列表项（富 SessionInfo 透出，供前端会话选择器展示/搜索）。

    前四个字段（id/name/path/modified）为初版契约，保持不变；
    其余为加法增强（modified 为 epoch 秒浮点，与初版一致）。
    """

    id: str
    name: str
    path: str
    modified: float
    message_count: int = 0
    first_message: str = ""
    cwd: str = ""
    parent_session_path: Optional[str] = None


class ListSessionsResult(RootModel):
    root: List[SessionListItem]


class DeleteSessionParams(NovaBaseModel):
    """deleteSession 参数：会话文件绝对路径。"""

    path: str


class DeleteSessionResult(NovaBaseModel):
    deleted: bool


class RenameSessionParams(NovaBaseModel):
    """renameSession 参数：会话文件绝对路径 + 新名字（trim 后为空 = 清除名字）。"""

    path: str
    name: str


class RenameSessionResult(NovaBaseModel):
    success: bool
    session_name: Optional[str] = None


class PromptParams(NovaBaseModel):
    text: str
    images: Optional[List[ImageContent]] = None
    expand_prompt_templates: StrictBool = True
    streaming_behavior: Optional[str] = None


class SteerParams(NovaBaseModel):
    text: str
    images: Optional[List[ImageContent]] = None


class FollowUpParams(NovaBaseModel):
    text: str
    images: Optional[List[ImageContent]] = None


class AbortResult(NovaBaseModel):
    success: bool
    reason: Optional[str] = None


class CancelRequestParams(NovaBaseModel):
    """cancelRequest 参数：要取消的正向调用 id（前端自增整数 id 空间）。"""

    id: int


class CancelRequestResult(NovaBaseModel):
    """cancelRequest 结果。cancelled=False 表示 id 不存在或调用已完成（幂等，非错误）。"""

    success: bool
    cancelled: bool


class TokenUsageSummary(NovaBaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


class SessionStateResult(NovaBaseModel):
    """``getSessionState`` 快照（前端 SessionSnapshot 的镜像）。"""

    session_id: str
    session_file: Optional[str] = None
    session_name: Optional[str] = None
    cwd: str
    model: Optional[ModelRef] = None
    thinking_level: str
    supports_thinking: bool
    available_thinking_levels: List[str]
    active_tools: List[str]
    message_count: int
    pending_message_count: int
    steering_messages: List[str]
    follow_up_messages: List[str]
    is_streaming: bool
    is_compacting: bool
    is_retrying: bool
    auto_retry_enabled: bool
    auto_compaction_enabled: bool
    steering_mode: str
    follow_up_mode: str
    # 项目信任决议（会话启动时裁决）——前端据此决定是否加载 project 级
    # ui/ 代码资产（jiti import 会执行包代码，与 Python 扩展同一门控）。
    project_trusted: bool = True
    # 当前叶节点（会话树导航/导出定位当前分支——树形数据的锚点）
    leaf_id: Optional[str] = None
    # 命令过滤（agent.yaml commands 允许集 + settings disabled_commands 排除集）——
    # 前端据此过滤 Node 扩展命令的分发与补全；None = 全部允许
    allowed_commands: Optional[List[str]] = None
    disabled_commands: List[str] = []
    # 角色能力选配报告（仅非 ok 项：missing / disabled_by_settings /
    # disabled_by_sdk）——"角色为什么少了个工具"的确定性答案来源
    capability_report: List[CapabilitySelection] = []
    # 当前角色与 persona override（footer/选择器的数据源）
    agent_name: Optional[str] = None
    persona_override: Optional[str] = None


class CompactParams(NovaBaseModel):
    custom_instructions: Optional[str] = None


# compact 的结果模型直接复用 core 压缩域的 CompactionResult（同形不重复定义——
# handler 返回 core 实例，dispatch 实例直通；重复定义会让类型校验错位）


class SetSessionNameParams(NovaBaseModel):
    name: str


class SetSessionNameResult(NovaBaseModel):
    success: bool
    session_name: Optional[str] = None


QueueModeValue = Literal["all", "one-at-a-time"]


class SetSteeringModeParams(NovaBaseModel):
    mode: QueueModeValue


class SetSteeringModeResult(NovaBaseModel):
    success: bool
    steering_mode: str


class SetFollowUpModeParams(NovaBaseModel):
    mode: QueueModeValue


class SetFollowUpModeResult(NovaBaseModel):
    success: bool
    follow_up_mode: str


class GetContextUsageResult(NovaBaseModel):
    """``getContextUsage`` 返回（上下文用量估算；无模型/无窗口时字段缺省）。"""

    tokens: Optional[int] = None
    context_window: Optional[int] = None
    percent: Optional[float] = None


class ClearQueueResult(NovaBaseModel):
    steering: List[str] = []
    follow_up: List[str] = []


class SetLabelParams(NovaBaseModel):
    entry_id: str
    label: Optional[str] = None


class SetAutoRetryParams(NovaBaseModel):
    enabled: StrictBool


class SetAutoRetryResult(NovaBaseModel):
    success: bool
    auto_retry_enabled: bool


class SetAutoCompactionEnabledParams(NovaBaseModel):
    enabled: StrictBool


class SetAutoCompactionEnabledResult(NovaBaseModel):
    success: bool
    auto_compaction_enabled: bool


class SetActiveToolsParams(NovaBaseModel):
    tool_names: Optional[List[str]] = None
    tools: Optional[List[str]] = None


class SetActiveToolsResult(NovaBaseModel):
    success: bool
    active_tools: List[str]


class NavigateTreeParams(NovaBaseModel):
    target_id: str
    options: Optional[Dict[str, Any]] = None


class ForkParams(NovaBaseModel):
    entry_id: str
    position: Literal["before", "after"] = "before"


class SessionStatsResult(NovaBaseModel):
    session_id: str = ""
    session_file: Optional[str] = None
    user_messages: int = 0
    assistant_messages: int = 0
    tool_calls: int = 0
    tool_results: int = 0
    total_messages: int = 0
    tokens: Optional[TokenUsageSummary] = None
    cost: float = 0.0
    cache_waste: Optional[Any] = None


class GetSessionEntriesParams(NovaBaseModel):
    """条目分页参数（缺省全量——旧客户端兼容）；offset/limit 均为条目数。"""

    offset: int = 0
    limit: int = 0  # 0 = 全量


class GetSessionEntriesResult(NovaBaseModel):
    entries: List[Dict[str, Any]]
    total: int = 0
    offset: int = 0


class SyncSessionParams(NovaBaseModel):
    """原子同步参数：分页（缺省全量）+ entries 段开关。

    ``include_entries=False``：客户端只要转录段（items）与状态——树导航
    需要条目图时再按需拉取（getSessionEntries/getSessionTree），不为
    不需要的数据付带宽。
    """

    entries_offset: int = 0
    entries_limit: int = 0  # 0 = 全量
    items_offset: int = 0
    items_limit: int = 0  # 0 = 全量
    include_entries: bool = True


class SyncSessionResult(NovaBaseModel):
    """原子同步快照：状态 + 条目页 + item 页 + 事件高水位。

    高水位语义：快照返回时 ``event_seq`` 之前的事件已全部反映在本快照
    内——前端丢弃 ``seq <= eventSeq`` 的增量事件即完成精确对账（单循环
    上快照装配与 seq 读取之间无 await，天然原子）。

    ``entries`` 是**条目图接口**（树导航载体：fork/navigateTree 按条目 id、
    parent_id 链住在条目上）；``items`` 是**转录段**（当前分支条目经归约
    纯映射的 item 清单 + 末页附在飞 item——重连客户端据此对齐在飞流式/
    执行的 started 状态，后续 delta 才对得上号）。两者分工：树导航读
    entries，转录渲染读 items。
    """

    state: SessionStateResult
    entries: List[Dict[str, Any]]
    total: int
    entries_offset: int = 0
    # SerializeAsAny：序列化按运行时实际类型 dump（包级变体全字段落线）；
    # WireItem：校验方向按 type 判别到具体变体——出参归一的
    # model_validate 重建时子类字段不丢（基类重建会剥掉 text 等字段）。
    items: List[SerializeAsAny[WireItem]] = Field(default_factory=list)
    total_items: int = 0
    items_offset: int = 0
    event_seq: int = 0


class CloneSessionResult(NovaBaseModel):
    success: bool
    cancelled: Optional[bool] = None
    session_id: Optional[str] = None
    session_file: Optional[str] = None


class ExportSessionParams(NovaBaseModel):
    path: str


class ExportSessionResult(NovaBaseModel):
    exported_to: str


class ImportSessionParams(NovaBaseModel):
    path: str
    cwd: Optional[str] = None


class ImportSessionResult(NovaBaseModel):
    success: bool
    cancelled: Optional[bool] = None
    session_id: Optional[str] = None
    session_name: Optional[str] = None


class AgentListItem(NovaBaseModel):
    name: str


class ListAgentsResult(RootModel):
    root: List[AgentListItem]


class ChangeAgentParams(NovaBaseModel):
    name: str


class ChangeAgentResult(NovaBaseModel):
    agent_name: str
    available_tools: List[ToolInfo]


class SaveAgentParams(NovaBaseModel):
    """``saveAgent``：物化当前生效状态为组合声明 yaml。

    ``name`` 缺席 = 就地/影子保存当前角色；提供 = save-as 新名（写 user 级）。
    """

    name: Optional[str] = None


class SaveAgentResult(NovaBaseModel):
    name: str
    saved_to: str
    # True = 包来源不可写，已影子写到 user 级（包内 yaml 未动）
    shadowed: bool = False


class GetToolsResult(NovaBaseModel):
    tools: List[ToolInfo]


# ---------------------------------------------------------------------------
# model 域
# ---------------------------------------------------------------------------


class ModelListItem(NovaBaseModel):
    provider: str
    id: str
    name: str
    available: bool
    reasoning: bool = False


class ListModelsResult(NovaBaseModel):
    models: List[ModelListItem]


class SetModelParams(NovaBaseModel):
    model: Union[str, Dict[str, Any]]


ThinkingLevelValue = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


class SetThinkingLevelParams(NovaBaseModel):
    level: ThinkingLevelValue


class SetThinkingLevelResult(NovaBaseModel):
    success: bool
    thinking_level: str


class CycleThinkingLevelResult(NovaBaseModel):
    success: bool
    thinking_level: Optional[str] = None
    reason: Optional[str] = None


class CycleModelParams(NovaBaseModel):
    direction: Literal["forward", "backward"] = "forward"


class CycleModelResult(NovaBaseModel):
    success: bool
    model: Optional[ModelRef] = None
    thinking_level: Optional[str] = None
    is_scoped: Optional[bool] = None


class ScopedModelItem(NovaBaseModel):
    provider: str
    id: str
    thinking_level: Optional[str] = None


class ListScopedModelsResult(NovaBaseModel):
    models: List[ScopedModelItem]


class ScopedModelInput(NovaBaseModel):
    provider: str
    model_id: Optional[str] = None
    id: Optional[str] = None
    thinking_level: Optional[str] = None


class SetScopedModelsParams(NovaBaseModel):
    models: List[ScopedModelInput]


class SetScopedModelsResult(NovaBaseModel):
    success: bool
    count: int


# ---------------------------------------------------------------------------
# auth 域
# ---------------------------------------------------------------------------


class CredentialInfo(NovaBaseModel):
    provider: Optional[str] = None
    type: Optional[str] = None


class GetAuthStatusResult(NovaBaseModel):
    credentials: List[CredentialInfo]


class ProviderParams(NovaBaseModel):
    provider: str


class SetApiKeyParams(NovaBaseModel):
    provider: str
    api_key: Optional[str] = None


class ProviderResult(NovaBaseModel):
    success: bool
    provider: str


class LoginParams(NovaBaseModel):
    provider: str
    auth_type: Literal["api_key", "oauth"] = "oauth"


class LoginResult(NovaBaseModel):
    success: bool
    provider: str
    type: Optional[str] = None


# ---------------------------------------------------------------------------
# resources 域
# ---------------------------------------------------------------------------


class PromptTemplateInfo(NovaBaseModel):
    name: str = ""
    description: str = ""
    argument_hint: Optional[str] = None
    source: Optional[str] = None


class ListPromptTemplatesResult(NovaBaseModel):
    prompts: List[PromptTemplateInfo]


class SkillInfo(NovaBaseModel):
    name: str = ""
    description: str = ""
    file_path: Optional[str] = None
    source_label: Optional[str] = None


class ListSkillsResult(NovaBaseModel):
    skills: List[SkillInfo]


# ---------------------------------------------------------------------------
# settings 域
# ---------------------------------------------------------------------------


class GetSettingsParams(NovaBaseModel):
    cwd: Optional[str] = None


class GetSettingsResult(NovaBaseModel):
    settings: Dict[str, Any]


class UpdateSettingsParams(NovaBaseModel):
    settings: Dict[str, Any]
    cwd: Optional[str] = None


class UpdateSettingsResult(NovaBaseModel):
    success: bool
    settings: Dict[str, Any]


class SetResourceExclusionParams(NovaBaseModel):
    """资源管控意图级方法的入参（名字级：tools / user_tools）。"""

    resource_type: str
    name: str
    cwd: Optional[str] = None


class SetResourceExclusionResult(NovaBaseModel):
    success: bool
    patterns: List[str]


class AgentEntry(NovaBaseModel):
    """agents 注册表条目（getAgents / /agent 选择器数据源）。"""

    name: str
    description: str = ""
    scope: str = ""
    origin: str = ""
    current: bool = False


class GetAgentsResult(NovaBaseModel):
    agents: List[AgentEntry] = []


class PersonaEntry(NovaBaseModel):
    """persona 注册表条目（getPersonas / /persona 选择器数据源）。"""

    name: str
    path: str = ""
    scope: str = ""
    origin: str = ""
    is_override: bool = False


class GetPersonasResult(NovaBaseModel):
    personas: List[PersonaEntry] = []
    override: Optional[str] = None


class SetPersonaOverrideParams(NovaBaseModel):
    """persona override 设置/清除（name 缺席或 null = 清除，恢复角色默认装配）。"""

    name: Optional[str] = None


class SetPersonaOverrideResult(NovaBaseModel):
    success: bool
    persona_override: Optional[str] = None


class AppendEntryParams(NovaBaseModel):
    """appendEntry：追加 custom 条目（持久化 + entry_appended 事件）。"""

    custom_type: str
    data: Optional[Any] = None


class AppendEntryResult(NovaBaseModel):
    success: bool
    entry_id: str


# ---------------------------------------------------------------------------
# system 域
# ---------------------------------------------------------------------------


class CommandInfo(NovaBaseModel):
    name: str
    description: Optional[str] = None
    source: str
    source_info: Optional[Any] = None


class GetCommandsResult(NovaBaseModel):
    commands: List[CommandInfo]


class ShortcutInfo(NovaBaseModel):
    shortcut: str
    description: Optional[str] = None
    extension_path: Optional[str] = None


class GetShortcutsResult(NovaBaseModel):
    shortcuts: List[ShortcutInfo]


class InvokeShortcutParams(NovaBaseModel):
    shortcut: str


class ExtensionFlagInfo(NovaBaseModel):
    name: str
    description: Optional[str] = None
    type: Optional[str] = None
    default: Optional[Any] = None
    value: Optional[Any] = None
    extension_path: Optional[str] = None


class GetExtensionFlagsResult(NovaBaseModel):
    flags: List[ExtensionFlagInfo]


class SetExtensionFlagParams(NovaBaseModel):
    name: str
    value: Optional[Any] = None


class SetExtensionFlagResult(NovaBaseModel):
    success: bool
    name: str
    value: Optional[Any] = None


# ---------------------------------------------------------------------------
# user_tools 域
# ---------------------------------------------------------------------------


class ListUserToolsResult(RootModel):
    root: List[Dict[str, Any]]


class InvokeUserToolParams(NovaBaseModel):
    name: str
    params: Optional[Dict[str, Any]] = None


class InvokeUserToolResult(NovaBaseModel):
    message: Dict[str, Any]


class AbortUserToolParams(NovaBaseModel):
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# package 域
# ---------------------------------------------------------------------------


class PkgParams(NovaBaseModel):
    local: StrictBool = False


class PkgInstallParams(NovaBaseModel):
    source: str
    local: StrictBool = False


class PkgNameParams(NovaBaseModel):
    name_or_source: str
    local: StrictBool = False


class PkgUninstallResult(NovaBaseModel):
    success: bool
    messages: List[str] = []


class PkgUpdateResult(RootModel):
    root: List[Dict[str, Any]]


class PackageUpdateItem(NovaBaseModel):
    source: str
    display_name: str
    type: str = "git"
    scope: Optional[str] = None


class PkgCheckUpdatesResult(NovaBaseModel):
    updates: List[PackageUpdateItem]
