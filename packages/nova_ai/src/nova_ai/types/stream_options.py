"""
流选项类型定义

纯代码构造、跨包传递的选项容器，从不从 JSON parse：
dataclass 的构造签名即契约（传错字段直接 TypeError），
无需 Pydantic 的运行时校验，也避免 Callable 字段的 schema hack。
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

from ..signal import AbortSignal
from .aliases import ProviderEnv, ProviderHeaders
from .enums import CacheRetention, ThinkingLevel, Transport

if TYPE_CHECKING:
    from .model import Model


@dataclass
class ProviderResponse:
    """HTTP 响应元数据"""

    status: int
    headers: Dict[str, str]


@dataclass
class ThinkingBudgets:
    """各思考级别的 token 预算"""

    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


@dataclass
class StreamOptions:
    """流式选项"""

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    api_key: Optional[str] = None
    transport: Optional[Transport] = None
    cache_retention: Optional[CacheRetention] = None
    session_id: Optional[str] = None
    # 自定义请求头；值为 None 表示抑制同名默认头
    headers: Optional[ProviderHeaders] = None
    metadata: Optional[Dict[str, Any]] = None
    # Provider 级环境变量，优先级高于进程环境变量
    env: Optional[ProviderEnv] = None
    # 请求超时（秒），对应 OpenAI Python SDK 的 timeout 参数
    timeout: Optional[float] = None
    # WebSocket 连接握手超时（毫秒），仅用于支持 WebSocket 传输的 provider
    websocket_connect_timeout_ms: Optional[int] = None
    # 最大重试次数，对应 OpenAI Python SDK 的 max_retries 参数
    max_retries: Optional[int] = None
    # 单次重试等待上限（毫秒）。用于封顶服务器通过 Retry-After
    # 等信号要求的重试等待时长；约定默认 60000（60s），0 表示不封顶。
    # 仅供实现了自有重试循环的 provider 消费（如未来的
    # codex-responses 移植）；当前 openai-completions provider
    # 的重试由 OpenAI SDK 内部管理，不读取此字段。
    max_retry_delay_ms: Optional[int] = None

    # 运行时回调/信号，不参与任何序列化
    signal: Optional[AbortSignal] = None
    on_payload: Optional[Callable[[Any, "Model"], Optional[Any]]] = None
    on_response: Optional[Callable[[ProviderResponse, Any], None]] = None
    # Models 层专属（对齐 TS ModelsStreamTransforms.transformHeaders）：
    # 在 auth/model/options headers 合并完成后、provider 派发前运行一次，
    # 可同步或异步返回新的 headers；``Models`` 负责消费并在派发前移除，
    # provider 实现永远不会看到该字段。
    transform_headers: Optional[Callable[[ProviderHeaders], Any]] = None


@dataclass
class SimpleStreamOptions(StreamOptions):
    """简单流式选项（带推理配置）"""

    reasoning: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
