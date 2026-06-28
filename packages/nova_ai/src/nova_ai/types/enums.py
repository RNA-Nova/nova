"""
枚举类型定义
"""

from enum import Enum
from typing import Union, Dict, Optional


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
    MISTRAL = "mistral"
    MINIMAX = "minimax"
    MINIMAX_CN = "minimax-cn"
    HUGGINGFACE = "huggingface"
    OPENCODE = "opencode"
    KIMI_CODING = "kimi-coding"
    VOLCENGINE = "volcengine"


# 允许任意字符串值的 Provider 类型
Provider = Union[KnownProvider, str]


class StopReason(str, Enum):
    """停止原因"""
    STOP = "stop"       # 正常结束
    LENGTH = "length"   # 达到长度限制
    TOOL_USE = "toolUse"  # 触发工具调用
    ERROR = "error"     # 发生错误
    ABORTED = "aborted"  # 被中止


class ThinkingLevel(str, Enum):
    """思考级别"""
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class CacheRetention(str, Enum):
    """缓存保留策略"""
    NONE = "none"
    SHORT = "short"
    LONG = "long"


class Transport(str, Enum):
    """传输协议"""
    SSE = "sse"
    WEBSOCKET = "websocket"
    AUTO = "auto"


class ThinkingFormat(str, Enum):
    """思考格式（用于不同提供商）"""
    OPENAI = "openai"      # 使用 reasoning_effort
    ZAI = "zai"            # 使用 thinking: { type: "enabled" }
    QWEN = "qwen"          # 使用 enable_thinking: boolean
    QWEN_CHAT_TEMPLATE = "qwen-chat-template"  # 使用 chat_template_kwargs
    DEEPSEEK = "deepseek"  # 使用 thinking: { type: "enabled" } + reasoning_effort
    OPENROUTER = "openrouter"  # 使用 reasoning: { effort: ... }
    TOGETHER = "together"  # 使用 reasoning: { enabled: bool } + reasoning_effort


# 思考级别映射：将 pi 思考级别映射到提供商/模型特定值
# 缺失的键使用提供商默认值，null 表示该级别不受支持
ThinkingLevelMap = Dict[str, Optional[str]]
