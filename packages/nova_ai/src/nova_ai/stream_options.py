"""
流选项类型定义

纯代码构造、跨包传递的选项容器，从不从 JSON parse：
dataclass 的构造签名即契约（传错字段直接 TypeError），
无需 Pydantic 的运行时校验，也避免 Callable 字段的 schema hack。
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from nova_protocol import (
    AbortSignal,
    CacheRetention,
    Model,
    ProviderEnv,
    ProviderHeaders,
    ThinkingLevel,
    Transport,
)


@dataclass(frozen=True, kw_only=True)
class ProviderResponse:
    """HTTP 响应元数据（不可变值对象——规则 5）。"""

    status: int
    headers: Dict[str, str]


@dataclass(frozen=True, kw_only=True)
class ModelIdentity:
    """钩子面的模型归因身份（白名单摘要——敏感字段不住上来）。

    ``on_payload``/``on_response`` 的第二参：只含归因三字段（哪个 provider
    的哪个模型、在哪个端点）。与 ``Model`` 是"白名单重建"而非裁剪副本——
    ``Model`` 新增字段（含 ``headers`` 等敏感字段）天然不进本类型：给是
    主动的、泄是被动的方向在此反转（security 出口纪律）。
    """

    provider: str
    id: str
    base_url: str

    @classmethod
    def from_model(cls, model: Model) -> "ModelIdentity":
        """从完整 ``Model`` 构造归因摘要（钩子调用点的唯一构造通道）。

        base_url 剥 userinfo（``https://user:pass@host`` 的内嵌凭据不出
        钩子面——白名单三字段的最后一个泄漏缝）。
        """
        base_url = model.base_url
        parts = urlsplit(base_url)
        if parts.username or parts.password:
            netloc = parts.hostname or ""
            if parts.port is not None:
                netloc = f"{netloc}:{parts.port}"
            base_url = urlunsplit(
                (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
            )
        return cls(provider=model.provider, id=model.id, base_url=base_url)


@dataclass(frozen=True, kw_only=True)
class ThinkingBudgets:
    """各思考级别的 token 预算（不可变值对象——AGENTS.md 数据建模规则 5）"""

    minimal: Optional[int] = None
    low: Optional[int] = None
    medium: Optional[int] = None
    high: Optional[int] = None


@dataclass
class StreamOptions:
    """流式选项"""

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # repr 防线（security 纪律）：api_key/headers 含密，不进 dataclass 默认 repr
    api_key: Optional[str] = field(default=None, repr=False)
    transport: Optional[Transport] = None
    cache_retention: Optional[CacheRetention] = None
    session_id: Optional[str] = None
    # 自定义请求头；值为 None 表示抑制同名默认头
    headers: Optional[ProviderHeaders] = field(default=None, repr=False)
    metadata: Optional[Dict[str, Any]] = None
    # Provider 级环境变量，优先级高于进程环境变量
    env: Optional[ProviderEnv] = None
    # 请求超时（秒）：连接建立后的 read/idle 相耐心——connect/pool 相归
    # 引擎上限（api_impls/_shared/timeouts.CONNECT_TIMEOUT_S），本字段管不到。
    # None = 引擎默认（DEFAULT_STREAM_TIMEOUT_S=300s）；0 = 显式关闭 read 相
    # 与 idle 看门狗（用户自负——connect 上限仍在）。对应 SDK 的 timeout 参数
    timeout: Optional[float] = None
    # 最大重试次数，对应 OpenAI Python SDK 的 max_retries 参数
    max_retries: Optional[int] = None
    # 单次重试等待上限（毫秒）。用于封顶服务器通过 Retry-After
    # 等信号要求的重试等待时长；约定默认 60000（60s），0 表示不封顶。
    # 由 openai-completions 的 retry_provider_request 消费——SDK 内建
    # 重试不可被 abort 打断，请求层重试已归 _shared/retry.py 接管。
    max_retry_delay_ms: Optional[int] = None

    # 采样参数透传（合并进请求体顶层，自定义键覆盖命名请求字段；
    # 对齐 TS samplingParams）
    sampling_params: Optional[Dict[str, Any]] = None

    # 运行时回调/信号，不参与任何序列化
    signal: Optional[AbortSignal] = None
    # 请求体/响应观测钩子；on_payload 第一参为自由负载（逐 provider 的请求体
    # 形状，pi unknown 对位）；第二参为 ModelIdentity 归因摘要——整 Model
    # 不出钩子面（headers 等敏感字段防泄，security 出口纪律）；返回值
    # Any = 可同步返回或返回 awaitable（实现侧 isawaitable 兼容）
    on_payload: Optional[Callable[[Any, ModelIdentity], Any]] = None
    on_response: Optional[Callable[[ProviderResponse, ModelIdentity], Any]] = None
    # ModelsManager 层专属（对齐 TS ModelsStreamTransforms.transformHeaders）：
    # 在 auth/model/options headers 合并完成后、provider 派发前运行一次，
    # 可同步或异步返回新的 headers；``ModelsManager`` 负责消费并在派发前移除，
    # provider 实现永远不会看到该字段。
    transform_headers: Optional[Callable[[ProviderHeaders], Any]] = None


@dataclass
class SimpleStreamOptions(StreamOptions):
    """简单流式选项（带推理配置）"""

    reasoning: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
