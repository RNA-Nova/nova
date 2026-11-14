"""
Provider 错误的归一（``docs/crosscutting/diagnostics-modeling.md`` 归一枢纽）。

两件套：

- ``normalize_provider_error`` / ``format_provider_error``：HTTP 错误体的
  形状探测与展示拼装（代理/网关后面的端点可能返回非 2xx 响应，其 body 无法
  被 provider SDK 折叠进 `error.message`；SDK 错误对象仍携带状态与 body，
  字段名因 SDK 而异——Mistral、`openai`、AWS Bedrock 等）；
- ``normalize_error`` → ``NormalizedError``：全栈唯一错误分类函数——异常
  → (kind, message, retryable, ...)，六级阶梯（自声明 → 溢出词表 → 限额
  词表 → SDK 错误类/状态码/传输链 → 限流文案 → ``INTERNAL``），下游只查
  kind，不再匹配文案（铁律二）。分类词表按家族集中本模块（单一正典源），
  ``overflow.py`` 等消费方 import 复用。
"""

import json
import math
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, List, Optional

import httpx
import httpx2
from nova_ai.utils.sanitize import REDACTED_SECRET, redact_secrets
from nova_protocol import RETRYABLE_ERROR_KINDS, NovaErrorKind

MAX_PROVIDER_ERROR_BODY_CHARS = 4000
MAX_NORMALIZED_MESSAGE_CHARS = 2048
"""归一消息的用户面截断（codex ``ERROR_MESSAGE_UI_MAX_BYTES`` 对位，字符近似）。"""


@dataclass(frozen=True, kw_only=True)
class NormalizedProviderError:
    """标准化后的 provider 错误信息（不可变值对象——规则 5）。"""

    message: str
    status: Optional[int] = None
    body: Optional[str] = None
    message_carries_body: bool = False


def _safe_json_stringify(value: Any) -> str:
    """安全 JSON 序列化，失败时回退到 str()。"""
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def _is_non_empty_object(value: Any) -> bool:
    return isinstance(value, dict) and len(value) > 0


def _truncate_error_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"


def status_code_of(error: Any) -> Optional[int]:
    """按已知 SDK 字段顺序提取 HTTP 状态码。"""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    status = getattr(error, "status", None)
    if isinstance(status, int):
        return status

    return None


def _pick_body_text(error: Any) -> Optional[str]:
    """按已知 SDK 字段顺序提取错误 body 文本。"""
    body = getattr(error, "body", None)
    if isinstance(body, str):
        return body

    error_body = getattr(error, "error", None)
    if _is_non_empty_object(error_body):
        return _safe_json_stringify(error_body)

    return None


def _extract_body(error: Any) -> Optional[str]:
    """提取并截断错误 body。"""
    body_text = _pick_body_text(error)
    if body_text is None:
        return None
    trimmed = body_text.strip()
    if len(trimmed) == 0:
        return None
    return _truncate_error_text(trimmed, MAX_PROVIDER_ERROR_BODY_CHARS)


def with_cause_detail(message: str, chain_start: Optional[BaseException]) -> str:
    """把 ``__cause__`` 链最深层的原因拼进 message（对齐 TS ``withCauseDetail``）。

    连接级失败没有 status/body,SDK 把真因替换成通用文案（openai
    ``"Connection error."``）——真因在 cause 链深处（httpx 的
    ``ConnectError`` 裹着 ECONNREFUSED/DNS/TLS)。拼最深层让只看消息的
    调用方（用户面/会话落盘）保留排查线索。最深 5 层、带环保护；底层
    异常带 ``code`` 属性且文案未含时括注。
    """
    deepest: Optional[BaseException] = None
    current = chain_start
    seen = set()
    depth = 0
    while current is not None and depth < 5 and id(current) not in seen:
        seen.add(id(current))
        deepest = current
        current = current.__cause__
        depth += 1
    if deepest is None:
        return message
    detail = (str(deepest) or type(deepest).__name__).strip()
    code = getattr(deepest, "code", None)
    if isinstance(code, (str, int)) and str(code) not in detail:
        detail = f"{detail} ({code})" if detail else str(code)
    if not detail or detail in message:
        return message
    return f"{message}: {detail}" if message else detail


def normalize_provider_error(error: Any) -> NormalizedProviderError:
    """将任意 provider SDK 错误标准化为结构化对象。"""
    if not isinstance(error, Exception):
        message = _safe_json_stringify(error)
        return NormalizedProviderError(message=message, message_carries_body=False)

    status = status_code_of(error)
    body = _extract_body(error)
    message = str(error)
    # 连接级错误（无 status/body）拼 cause 链最深层真因（对齐 pi 4c992154d）
    if status is None and body is None:
        message = with_cause_detail(message, error.__cause__)
    message_carries_body = body is None or body in message

    return NormalizedProviderError(
        status=status,
        body=body,
        message=message,
        message_carries_body=message_carries_body,
    )


def format_provider_error(
    norm: NormalizedProviderError, prefix: Optional[str] = None
) -> str:
    """将标准化错误格式化为展示字符串。"""
    if norm.message_carries_body or norm.status is None or norm.body is None:
        if prefix is not None and norm.status is not None:
            return f"{prefix} ({norm.status}): {norm.message}"
        return norm.message

    if prefix is not None:
        return f"{prefix} ({norm.status}): {norm.body}"
    return f"{norm.status}: {norm.body}"


# ---------------------------------------------------------------------------
# 错误分类词表（按家族集中——单一正典源，每条 pattern 注出处）
# ---------------------------------------------------------------------------

# 溢出错误模式：匹配输入超出模型上下文窗口时的错误消息（对齐 pi overflow 模式库）
OVERFLOW_PATTERNS: List[re.Pattern] = [
    re.compile(r"prompt (?:is )?too long", re.IGNORECASE),  # Anthropic / z.ai
    re.compile(r"prompt exceeds max length", re.IGNORECASE),  # z.ai CN
    re.compile(r"request_too_large", re.IGNORECASE),  # Anthropic HTTP 413
    re.compile(r"input is too long for requested model", re.IGNORECASE),  # Bedrock
    re.compile(r"exceeds the context window", re.IGNORECASE),  # OpenAI
    re.compile(
        r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d,]+ tokens?|\s*\([\d,]+\))",
        re.IGNORECASE,
    ),  # OpenAI-compatible proxies
    re.compile(r"context window exceeded", re.IGNORECASE),  # LiteLLM 预检
    re.compile(r"input token count.*exceeds the maximum", re.IGNORECASE),  # Google
    re.compile(r"maximum prompt length is \d+", re.IGNORECASE),  # xAI
    re.compile(r"reduce the length of the messages", re.IGNORECASE),  # Groq
    re.compile(r"maximum context length is \d+ tokens", re.IGNORECASE),  # OpenRouter
    re.compile(
        r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?",
        re.IGNORECASE,
    ),  # OpenRouter/Poolside
    re.compile(
        r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)",
        re.IGNORECASE,
    ),  # Together AI
    re.compile(r"exceeds the limit of \d+", re.IGNORECASE),  # GitHub Copilot
    re.compile(r"exceeds the available context size", re.IGNORECASE),  # llama.cpp
    re.compile(r"greater than the context length", re.IGNORECASE),  # LM Studio
    re.compile(r"context window exceeds limit", re.IGNORECASE),  # MiniMax
    re.compile(r"exceeded model token limit", re.IGNORECASE),  # Kimi For Coding
    re.compile(
        r"too large for model with \d+ maximum context length", re.IGNORECASE
    ),  # Mistral
    re.compile(
        r"prompt has [\d,]+ tokens?, but the configured context size is [\d,]+ tokens?",
        re.IGNORECASE,
    ),  # DS4
    re.compile(r"model_context_window_exceeded", re.IGNORECASE),  # z.ai finish_reason
    re.compile(
        r"prompt too long; exceeded (?:max )?context length", re.IGNORECASE
    ),  # Ollama
    re.compile(r"range of input length should be", re.IGNORECASE),  # DashScope/Qwen
    re.compile(r"context[_ ]length[_ ]exceeded", re.IGNORECASE),  # Generic
    re.compile(r"too many tokens", re.IGNORECASE),  # Generic
    re.compile(r"token limit exceeded", re.IGNORECASE),  # Generic
    re.compile(r"context[_ ]?window[_ ]?exceeded", re.IGNORECASE),  # LiteLLM 等
    # HTTP 413 是请求大小溢出；只匹配消息前缀，不匹配正文里偶然提到的状态码。
    re.compile(
        r"^(?:[\w -]+ API error \(413\):|(?:Error code:\s*)?413(?:\s|:|-|$))",
        re.IGNORECASE,
    ),
]

CEREBRAS_BODYLESS_OVERFLOW_PATTERN = re.compile(
    r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.IGNORECASE
)
"""Cerebras 特有的无 body 400/413 溢出形状，不得全局套用。"""

# 非溢出错误模式（限流、服务器错误等）——命中即否决溢出判定
NON_OVERFLOW_PATTERNS: List[re.Pattern] = [
    re.compile(r"^(Throttling error|Service unavailable):", re.IGNORECASE),  # Bedrock
    re.compile(r"rate limit", re.IGNORECASE),  # Generic rate limiting
    re.compile(r"too many requests", re.IGNORECASE),  # Generic HTTP 429
]

# 额度/订阅用尽模式（**不可重试家族**——与"临时限流"分流；先于状态码判定，
# codex 的 429 usage_limit_reached 特判同款）。
# 出处：harness RetryController non_retryable 清单 / pi NON_RETRYABLE_PROVIDER_LIMIT_ERROR_PATTERN
USAGE_LIMIT_PATTERNS: List[re.Pattern] = [
    re.compile(r"insufficient_quota", re.IGNORECASE),  # OpenAI quota
    re.compile(r"usage limit", re.IGNORECASE),  # 订阅额度
    re.compile(r"out of budget", re.IGNORECASE),  # 预算耗尽
    re.compile(r"quota exceeded", re.IGNORECASE),  # 通用配额
    re.compile(r"billing", re.IGNORECASE),  # 账单问题
    re.compile(r"available balance", re.IGNORECASE),  # 余额不足（DeepSeek 等）
]

# 临时限流文案（无状态码/无 SDK 类时的归类依据；有结构信息时结构优先）
RATE_LIMIT_TEXT_PATTERNS: List[re.Pattern] = [
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
]

# 超载文案（同上——anthropic overloaded_error / 各网关 529 文案）
OVERLOADED_TEXT_PATTERNS: List[re.Pattern] = [
    re.compile(r"overloaded", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# 归一产物与六级阶梯（normalize_error）
# ---------------------------------------------------------------------------


class LoginRequiredError(ValueError):
    """未配置凭据的请求级守卫错误（``LOGIN_REQUIRED`` 自声明——归一第 1 级
    消费；用户面可行动文案：环境变量或 /login）。"""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(
            f"No API key for provider: {provider}. "
            "Set the provider's API key env var, or run /login to authenticate."
        )
        self.nova_error_kind = NovaErrorKind.LOGIN_REQUIRED


class RequestTimeoutError(TimeoutError):
    """请求/刷新路径的显式超时（``REQUEST_TIMEOUT`` 自声明——归一第 1 级消费）。

    与 :class:`AbortedError` 彻底分家（诊断铁律三的对偶）：超时是系统判
    死亡、取消是用户判死亡，两者永不相冒。继承内建 ``TimeoutError``——
    传输重试的 ``TimeoutError`` 家族判定天然覆盖（可重试）。消息统一英文
    （诊断原文纪律，本地化建模）。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.nova_error_kind = NovaErrorKind.REQUEST_TIMEOUT


class StreamDisconnectedError(RuntimeError):
    """流在协议终态前断开（自声明 STREAM_DISCONNECTED，允许上层重试）。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.nova_error_kind = NovaErrorKind.STREAM_DISCONNECTED


@dataclass(frozen=True, kw_only=True)
class NormalizedError:
    """边界归一后的错误（运行时容器——数据建模规则 4/5：异常不进 Pydantic）。

    - ``kind`` 是全栈唯一分类真相（下游只查它，铁律二）；
    - ``message`` 面向用户（``MAX_NORMALIZED_MESSAGE_CHARS`` 截断）；
    - ``cause`` 仅诊断链——不上线不落盘（铁律六）。
    """

    kind: NovaErrorKind
    message: str
    retryable: bool
    retry_after_ms: Optional[float] = None
    http_status: Optional[int] = None
    domain: Optional[str] = None
    cause: Optional[BaseException] = None


def get_header(headers: Any, name: str) -> Optional[str]:
    """从 SDK 错误的 httpx Headers / dict 形态里取头（大小写不敏感）。"""
    if headers is None:
        return None
    try:
        if isinstance(headers, dict):
            lowered = name.lower()
            value = next(
                (v for k, v in headers.items() if str(k).lower() == lowered), None
            )
        else:
            value = headers.get(name)
    except AttributeError:
        return None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def headers_of(error: BaseException) -> Any:
    """取错误的响应头——SDK 错误形状知识的唯一归档点（边界归一）。

    openai/anthropic 的 ``APIStatusError`` 实例属性只有
    message/request/body/response/status_code/request_id（实证：
    openai/_exceptions.py:100-104，anthropic 同构）——头在
    ``.response.headers``；自家错误（HttpFetchError 等）直接带
    ``.headers``。两路都取不到返回 None（显式语义：该错误无头可读）。

    纪律：``getattr`` 带默认值落在第三方对象上，只许发生在本函数这类
    边界归一器内（data-modeling 规则 3 家族）；调用方一律经本函数。
    """
    headers = getattr(error, "headers", None)  # 自家形状先试
    if headers is not None:
        return headers
    response = getattr(error, "response", None)  # SDK 形状兜底
    if response is not None:
        return getattr(response, "headers", None)
    return None


def provider_error_metadata_raw(error: BaseException) -> Optional[str]:
    """探测 provider 错误对象的 ``error.error.metadata.raw`` 嵌套形状
    （部分 provider 的错误原文通道——形状不定，逐层缺席即 None）。

    形状知识的唯一归档点（边界归一器——规则 3 家族）；原文拼进消息前
    仍须过 ``redact_secrets``（供应商控制的文本，消费方纪律不变）。
    """
    try:
        raw = getattr(
            getattr(getattr(error, "error", None), "metadata", None),
            "raw",
            None,
        )
    except Exception:
        return None
    # 形状不定：只放行 str——注解 Optional[str] 的诚实由收口保证
    return raw if isinstance(raw, str) else None


def extract_retry_after_ms(headers: Any) -> Optional[float]:
    """``retry-after-ms`` / ``retry-after`` 头 → 有限毫秒数；非法值落本地退避。"""

    def _finite(value: float) -> Optional[float]:
        return value if math.isfinite(value) else None

    retry_after_ms = get_header(headers, "retry-after-ms")
    if retry_after_ms:
        try:
            return _finite(float(retry_after_ms))
        except ValueError:
            pass
    retry_after = get_header(headers, "retry-after")
    if retry_after:
        try:
            return _finite(float(retry_after) * 1000)
        except ValueError:
            try:
                return _finite(
                    (parsedate_to_datetime(retry_after).timestamp() - time.time())
                    * 1000
                )
            except Exception:
                return None
    return None


# 已知 SDK 错误类 → kind（顺序敏感：APITimeoutError 是 APIConnectionError
# 的子类，必须先判；openai/anthropic 层级同构同名，未收录的类名 getattr 跳过）
_SDK_CLASS_KIND = (
    ("APITimeoutError", NovaErrorKind.REQUEST_TIMEOUT),
    ("APIConnectionError", NovaErrorKind.HTTP_CONNECTION_FAILED),
    ("AuthenticationError", NovaErrorKind.UNAUTHORIZED),
    ("PermissionDeniedError", NovaErrorKind.UNAUTHORIZED),
    ("RateLimitError", NovaErrorKind.RATE_LIMITED),
    ("NotFoundError", NovaErrorKind.MODEL_NOT_FOUND),
    ("ConflictError", NovaErrorKind.OVERLOADED),
    ("OverloadedError", NovaErrorKind.OVERLOADED),
    ("BadRequestError", NovaErrorKind.BAD_REQUEST),
    ("UnprocessableEntityError", NovaErrorKind.BAD_REQUEST),
    ("InternalServerError", NovaErrorKind.UPSTREAM_ERROR),
)


def _sdk_class_kind(error: BaseException) -> Optional[NovaErrorKind]:
    """命中已知 SDK 错误类（惰性 import——SDK 未安装则跳过该家）。"""
    modules: List[Any] = []
    try:
        import openai

        modules.append(openai)
    except ImportError:
        pass
    try:
        import anthropic

        modules.append(anthropic)
    except ImportError:
        pass
    for module in modules:
        for class_name, kind in _SDK_CLASS_KIND:
            error_class = getattr(module, class_name, None)
            if error_class is not None and isinstance(error, error_class):
                return kind
    return None


def _status_kind(status: Optional[int]) -> Optional[NovaErrorKind]:
    """HTTP 状态码 → kind。"""
    if status is None:
        return None
    if status in (401, 403):
        return NovaErrorKind.UNAUTHORIZED
    if status == 404:
        return NovaErrorKind.MODEL_NOT_FOUND
    if status == 408:
        return NovaErrorKind.REQUEST_TIMEOUT
    if status in (409, 529):
        return NovaErrorKind.OVERLOADED
    if status == 429:
        return NovaErrorKind.RATE_LIMITED
    if status == 400:
        return NovaErrorKind.BAD_REQUEST
    if status >= 500:
        return NovaErrorKind.UPSTREAM_ERROR
    return None


def _transport_kind(error: BaseException) -> Optional[NovaErrorKind]:
    """单节点传输错误判定（内建超时/连接异常 + httpx/httpx2 TransportError）。

    openai/anthropic 的 APIConnectionError 不继承内建 ConnectionError——
    真实触发路径在 ``__cause__`` 链上（``_structured_kind_chain`` 负责链走，
    本函数只看当前节点）。
    """
    if isinstance(error, TimeoutError):
        return NovaErrorKind.REQUEST_TIMEOUT
    if isinstance(error, ConnectionError):
        return NovaErrorKind.HTTP_CONNECTION_FAILED
    if isinstance(error, (httpx.TimeoutException, httpx2.TimeoutException)):
        return NovaErrorKind.REQUEST_TIMEOUT
    if isinstance(error, (httpx.TransportError, httpx2.TransportError)):
        return NovaErrorKind.HTTP_CONNECTION_FAILED
    return None


def iter_cause_chain(error: BaseException):
    """沿 ``__cause__`` 链逐节点产出（**带环保护**——respx 的
    SideEffectError 会把 ConnectError 包成 A↔B 循环链，裸 walk 即死循环；
    Python 异常链在第三方库手里并不保证无环）。"""
    current: Optional[BaseException] = error
    seen: set = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


def _declared_kind_chain(error: BaseException) -> Optional[NovaErrorKind]:
    """自声明 kind 链走（``__cause__`` 外层→内层取最近一份自声明）。

    包装层（如 ``ModelsError``）通常不自带分类，底层领域异常的自声明
    不该被包装掩盖（codex ``#[from]`` 保源同款语义）。
    """
    for node in iter_cause_chain(error):
        declared = getattr(node, "nova_error_kind", None)
        if isinstance(declared, NovaErrorKind):
            return declared
    return None


def _structured_kind_chain(error: BaseException) -> Optional[NovaErrorKind]:
    """结构化分类链走（外层→内层先命中先赢；节点内 SDK 错误类 → 状态码
    → 传输错误序）。"""
    for node in iter_cause_chain(error):
        kind = (
            _sdk_class_kind(node)
            or _status_kind(status_code_of(node))
            or _transport_kind(node)
        )
        if kind is not None:
            return kind
    return None


def _matches_any(patterns: List[re.Pattern], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _is_overflow_text(text: str, provider: Optional[str] = None) -> bool:
    """溢出词表判定；Cerebras 无 body 特判只在该 provider 下生效。"""
    if _matches_any(NON_OVERFLOW_PATTERNS, text):
        return False
    return _matches_any(OVERFLOW_PATTERNS, text) or (
        provider == "cerebras"
        and CEREBRAS_BODYLESS_OVERFLOW_PATTERN.search(text) is not None
    )


def _http_status_chain(error: BaseException) -> Optional[int]:
    """沿 ``__cause__`` 链取第一个 HTTP 状态（遥测要的是真实那层的状态）。"""
    for node in iter_cause_chain(error):
        status = status_code_of(node)
        if status is not None:
            return status
    return None


def _retry_after_ms_chain(error: BaseException) -> Optional[float]:
    """沿 ``__cause__`` 链取第一份 Retry-After 退避（包装层不掩盖底层
    限流指引——与 kind/http_status 的链走对称）。"""
    for node in iter_cause_chain(error):
        value = extract_retry_after_ms(headers_of(node))
        if value is not None:
            return value
    return None


def normalize_error(
    error: Any, *, domain: Optional[str] = None, provider: Optional[str] = None
) -> NormalizedError:
    """任意异常 → ``NormalizedError``（六级阶梯，全栈唯一分类函数）。

    阶梯：**自声明**（``nova_error_kind``，沿 ``__cause__`` 链取最近一份
    ——包装层不掩盖底层分类）→ **溢出词表**（先于一切——溢出常披 400/413
    外衣）→ **限额词表**（429 的"额度用尽"与"临时限流"在此分流）→
    **SDK 错误类/状态码/传输错误**（沿 ``__cause__`` 链逐节点判定，外层
    先赢）→ **超载/限流文案** → ``INTERNAL``。

    **调用方不得传入取消异常**（``AbortedError`` 走自己的通道，铁律三）——
    catch 块先判 aborted 再归一。
    """
    if not isinstance(error, BaseException):
        return NormalizedError(
            kind=NovaErrorKind.INTERNAL,
            message=_truncate_error_text(
                redact_secrets(_safe_json_stringify(error)),
                MAX_NORMALIZED_MESSAGE_CHARS,
            ),
            retryable=False,
            domain=domain,
        )

    normalized_http = normalize_provider_error(error)
    status = _http_status_chain(error)
    # 词表匹配面：消息 + body（body 承载多数厂商的真文案；ModelsError 等
    # 包装层的消息经 _with_cause_detail 拼入底层原因，文本面自然覆盖链）
    text = normalized_http.message
    if normalized_http.body and normalized_http.body not in text:
        text = f"{text} {normalized_http.body}"

    declared_kind = _declared_kind_chain(error)
    kind: NovaErrorKind
    if declared_kind is not None:
        kind = declared_kind
    elif _is_overflow_text(text, provider):
        kind = NovaErrorKind.CONTEXT_OVERFLOW
    elif _matches_any(USAGE_LIMIT_PATTERNS, text):
        kind = NovaErrorKind.USAGE_LIMIT_EXCEEDED
    else:
        kind = (
            _structured_kind_chain(error)
            or (
                NovaErrorKind.OVERLOADED
                if _matches_any(OVERLOADED_TEXT_PATTERNS, text)
                else None
            )
            or (
                NovaErrorKind.RATE_LIMITED
                if _matches_any(RATE_LIMIT_TEXT_PATTERNS, text)
                else None
            )
            or NovaErrorKind.INTERNAL
        )

    # domain 缺省回落领域错误的自报归属（ModelsError.code 六域）；SDK 的
    # .code（provider 错误码）也可能命中——同为排查线索，可接受
    code = getattr(error, "code", None)

    return NormalizedError(
        kind=kind,
        # 出口清洗在截断前（防截断劈开 token 留半截）：用户面消息一律过
        # redact_secrets——分类用原文（词表不命中 token 形状），展示用净文
        message=_truncate_error_text(
            redact_secrets(format_provider_error(normalized_http)),
            MAX_NORMALIZED_MESSAGE_CHARS,
        ),
        retryable=kind in RETRYABLE_ERROR_KINDS,
        retry_after_ms=_retry_after_ms_chain(error),
        http_status=status,
        domain=domain or (code if isinstance(code, str) else None),
        cause=error,
    )
