"""
枚举类型定义
"""

from enum import Enum
from typing import Literal, Optional, TypedDict, Union


class KnownApi(str, Enum):
    """已知的 API 类型"""

    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_RESPONSES = "openai-responses"
    AZURE_OPENAI_RESPONSES = "azure-openai-responses"
    OPENAI_CODEX_RESPONSES = "openai-codex-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    BEDROCK_CONVERSE_STREAM = "bedrock-converse-stream"
    GOOGLE_GENERATIVE_AI = "google-generative-ai"
    GOOGLE_GEMINI_CLI = "google-gemini-cli"
    GOOGLE_VERTEX = "google-vertex"


# 允许任意字符串值的 API 类型
Api = Union[KnownApi, str]


class KnownProvider(str, Enum):
    """已知的服务提供商类型"""

    AMAZON_BEDROCK = "amazon-bedrock"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    GOOGLE_GEMINI_CLI = "google-gemini-cli"
    GOOGLE_ANTIGRAVITY = "google-antigravity"
    GOOGLE_VERTEX = "google-vertex"
    OPENAI = "openai"
    AZURE_OPENAI_RESPONSES = "azure-openai-responses"
    OPENAI_CODEX = "openai-codex"
    GITHUB_COPILOT = "github-copilot"
    XAI = "xai"
    GROQ = "groq"
    CEREBRAS = "cerebras"
    OPENROUTER = "openrouter"
    VERCEL_AI_GATEWAY = "vercel-ai-gateway"
    ZAI = "zai"
    ZAI_CODING_CN = "zai-coding-cn"
    MISTRAL = "mistral"
    MINIMAX = "minimax"
    MINIMAX_CN = "minimax-cn"
    HUGGINGFACE = "huggingface"
    OPENCODE = "opencode"
    OPENCODE_GO = "opencode-go"
    NVIDIA = "nvidia"
    MOONSHOTAI = "moonshotai"
    MOONSHOTAI_CN = "moonshotai-cn"
    KIMI_CODING = "kimi-coding"
    VOLCENGINE = "volcengine"


# 允许任意字符串值的 provider id 类型（对齐 TS ``ProviderId``；
# 与 ``nova_ai.models.Provider`` 运行时单元区分）
ProviderId = Union[KnownProvider, str]


class StopReason(str, Enum):
    """停止原因"""

    PENDING = "pending"  # 尚未收到 finish_reason（流中瞬态初值）
    STOP = "stop"  # 正常结束
    LENGTH = "length"  # 达到长度限制
    TOOL_USE = "toolUse"  # 触发工具调用
    ERROR = "error"  # 发生错误
    ABORTED = "aborted"  # 被中止


class ThinkingLevel(str, Enum):
    """思考级别（请求侧）。

    用于 ``SimpleStreamOptions.reasoning`` 等发给 provider 的选项。
    类型层面不含 ``off``：关闭思考用 ``reasoning=None``（不发送
    reasoning 参数）表达，因为各 provider 没有统一的 "off" 线值。
    """

    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ModelThinkingLevel(str, Enum):
    """思考级别（模型/状态侧）。

    用于模型能力声明（``thinking_level_map`` 的键、
    ``get_supported_thinking_levels`` / ``clamp_thinking_level``）与
    Agent / 会话状态（``AgentState.thinking_level``）。
    包含 ``off``：状态可以"处于"关闭，模型可以"支持"关闭。
    """

    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class CacheRetention(str, Enum):
    """缓存保留策略"""

    NONE = "none"
    SHORT = "short"
    LONG = "long"


class Transport(str, Enum):
    """传输协议"""

    SSE = "sse"
    WEBSOCKET = "websocket"
    WEBSOCKET_CACHED = "websocket-cached"
    AUTO = "auto"


class ThinkingFormat(str, Enum):
    """思考格式（用于不同提供商）"""

    OPENAI = "openai"  # 使用 reasoning_effort
    ZAI = "zai"  # 使用 thinking: { type: "enabled" }
    QWEN = "qwen"  # 使用 enable_thinking: boolean
    QWEN_CHAT_TEMPLATE = "qwen-chat-template"  # 使用 chat_template_kwargs
    DEEPSEEK = "deepseek"  # 使用 thinking: { type: "enabled" } + reasoning_effort
    OPENROUTER = "openrouter"  # 使用 reasoning: { effort: ... }
    TOGETHER = "together"  # 使用 reasoning: { enabled: bool } + reasoning_effort
    ANT_LING = "ant-ling"  # 仅当级别有显式映射时发送 reasoning: { effort }
    STRING_THINKING = "string-thinking"  # 顶层 thinking 字符串参数
    CHAT_TEMPLATE = "chat-template"  # 由 chat_template_kwargs 配置驱动（$var 变量替换）
    BASETEN = "baseten"  # 使用 chat_template_args（Record 形态）+ reasoning_effort


class ThinkingLevelMap(TypedDict, total=False):
    """思考级别映射：将 pi 思考级别映射到提供商/模型特定值（规则 10 声明）。

    缺键 = 使用提供商默认值；``None`` = 该级别不受支持。
    """

    off: Optional[str]
    minimal: Optional[str]
    low: Optional[str]
    medium: Optional[str]
    high: Optional[str]
    xhigh: Optional[str]
    max: Optional[str]

# 顶层思考预算字段名（vLLM / Qwen-DashScope+SGLang / llama.cpp；
# 对齐 TS ``ThinkingTokenBudgetField``）
ThinkingTokenBudgetField = Literal[
    "thinking_token_budget", "thinking_budget", "thinking_budget_tokens"
]
