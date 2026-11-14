"""Anthropic message threads（对齐 pi a758f3ae4——服务端会话续接）。

服务端保管线程会话：携带 session id 且缓存开启的请求可以**只发增量
消息**，从上一条回复续接，而不是每轮发全量历史。仍构建完整请求；
``prepare_thread_request`` 与上次请求比对，前缀未变则只发新增部分。

- 默认仅 provider=anthropic 且 baseUrl 在 api.anthropic.com 开启（丢未知
  请求字段的网关会静默丢会话）——``compat.supports_message_threads`` 覆盖；
- 续接被拒（过期/竞态/固定字段漂移）→ 以新线程全量重发一次；端点不认
  threads → 不带 ``thread`` 重发，会话级关停；
- ``StreamOptions.disable_continuation``：缓存预热类重放请求不顶替会话
  的线程状态；
- 会话终结经 ``cleanup_session_resources(session_id)`` 释放台账。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Union

from nova_async import AbortSignal, is_aborted
from nova_protocol import AssistantMessage, Context, Model

from ...session_resources import register_session_resource_cleanup
from ...utils.diagnostics import append_diagnostic
from ...utils.hash import short_hash
from .compat import ResolvedAnthropicCompat
from .messages import convert_messages

MESSAGE_THREADS_BETA = "message-threads-2026-08-12"

ThreadParam = Dict[str, Any]
"""``{"type": "create"}`` 或 ``{"type": "continue", "previous_message_id": ...}``。"""


@dataclass(frozen=True, kw_only=True)
class ThreadEntry:
    """下一次请求可续接的位置（对齐 pi ``ThreadEntry``）。"""

    # 上次全量请求的消息数 + 转换后的回复消息数
    prefix_count: int
    # ``hash_thread_prefix`` 对请求设置与前缀消息的指纹
    prefix_hash: str
    # 续接锚点（上一条回复的服务端 id）
    reply_id: str


@dataclass(frozen=True, kw_only=True)
class PreparedThreadRequest:
    """待发的线程请求（对齐 pi ``PreparedThreadRequest``）。"""

    session_id: str
    # params 副本 + 顶层 ``thread`` 字段（SDK 边界转 extra_body 上线）
    body: Dict[str, Any]


# 会话级台账：下一请求可续接的位置；"off" = 端点已拒 threads（不再携带）
_THREAD_STATE: Dict[str, Union[ThreadEntry, Literal["off"]]] = {}
_MAX_THREAD_STATE_ENTRIES = 10_000

# 续接被拒中以"换新线程"可解的错误码（过期/竞态/固定字段漂移——pi 原表）
_NEW_THREAD_ERRORS = frozenset(
    [
        "thread_not_found",
        "thread_already_continued",
        "thread_continue_in_flight",
        "thread_fingerprint_mismatch",
    ]
)


def _set_thread_state(
    session_id: str, value: Union[ThreadEntry, Literal["off"]]
) -> None:
    """写入会话线程状态（LRU 上限——对齐 pi setThreadState）。

    正常释放归 ``cleanup_session_resources``；上限兜底永不清理的调用方
    （如每会话新 id 的服务端）——最久未更新的线程在服务端多半已过期，
    丢失的代价只是下次请求新起线程。
    """
    _THREAD_STATE.pop(session_id, None)
    _THREAD_STATE[session_id] = value
    if len(_THREAD_STATE) > _MAX_THREAD_STATE_ENTRIES:
        oldest = next(iter(_THREAD_STATE))
        _THREAD_STATE.pop(oldest, None)


def _cleanup(session_id: Optional[str]) -> None:
    if session_id:
        _THREAD_STATE.pop(session_id, None)
    else:
        _THREAD_STATE.clear()


register_session_resource_cleanup(_cleanup)


def _normalize_for_hash(node: Any) -> Any:
    """消息归一（对齐 pi hashThreadPrefix 的 JSON replacer）：

    - ``cache_control`` 键剔除——缓存标记不改变消息语义却会移动（新消息
      带标记，旧消息脱标）；
    - 字符串 content 包装为单文本块——挂标记时字符串变单块、下轮又变回
      字符串，"hi" 与 ``[{"type": "text", "text": "hi"}]`` 同义。
    """
    if isinstance(node, dict):
        return {
            key: _normalize_for_hash(value)
            for key, value in node.items()
            if key != "cache_control"
        }
    if isinstance(node, list):
        return [_normalize_for_hash(item) for item in node]
    return node


def hash_thread_prefix(
    model: Model,
    params: Dict[str, Any],
    messages: List[Dict[str, Any]],
    betas: List[str],
) -> str:
    """``continue`` 所依赖一切的指纹（对齐 pi hashThreadPrefix）：

    - 设置（按发送形态）：端点（线程只活在一个端点上）与固定请求字段。
      continue 省略 ``system``/``tools`` 由服务端沿用线程的——只有这个
      校验能发现它们的变化（含缓存标记）；其余字段变化服务端以
      ``thread_fingerprint_mismatch`` 拒绝，此处先拦省一次废请求；
    - 消息：归一化后序列化（同义消息同指纹）。
    """
    settings = json.dumps(
        [
            model.provider,
            model.base_url,
            params.get("model"),
            params.get("system"),
            params.get("tools"),
            params.get("thinking"),
            params.get("output_config"),
            params.get("tool_choice"),
            betas,
            params.get("fallbacks"),
        ],
        sort_keys=True,
        default=str,
    )
    normalized_messages = [
        (
            {
                key: (
                    [{"type": "text", "text": value}]
                    if key == "content" and isinstance(value, str)
                    else _normalize_for_hash(value)
                )
                for key, value in message.items()
                if key != "cache_control"
            }
            if isinstance(message, dict)
            else message
        )
        for message in messages
    ]
    conversation = json.dumps(normalized_messages, sort_keys=True, default=str)
    return short_hash(settings + conversation)


def create_thread_request(
    session_id: str, params: Dict[str, Any]
) -> PreparedThreadRequest:
    """新线程请求（对齐 pi createThreadRequest）。"""
    return PreparedThreadRequest(
        session_id=session_id, body={**params, "thread": {"type": "create"}}
    )


def prepare_thread_request(
    model: Model,
    session_id: str,
    params: Dict[str, Any],
    betas: List[str],
) -> Optional[PreparedThreadRequest]:
    """决定本次请求的线程形态（对齐 pi prepareThreadRequest）：

    - 会话已关停（端点拒过）→ ``None``（普通请求）；
    - 前缀匹配（现有消息数 > 台账前缀且前缀指纹一致）→ continue：剥掉
      ``system``/``tools``，只发增量消息；
    - 否则新线程全量。
    """
    state = _THREAD_STATE.get(session_id)
    if state == "off":
        return None
    messages = params.get("messages") or []
    if (
        isinstance(state, ThreadEntry)
        and len(messages) > state.prefix_count
        and hash_thread_prefix(model, params, messages[: state.prefix_count], betas)
        == state.prefix_hash
    ):
        # 每条回复只能续接一次；取走即删也挡住并发请求重复续接
        _THREAD_STATE.pop(session_id, None)
        body = {
            key: value
            for key, value in params.items()
            if key not in ("system", "tools")
        }
        body["messages"] = messages[state.prefix_count :]
        body["thread"] = {
            "type": "continue",
            "previous_message_id": state.reply_id,
        }
        return PreparedThreadRequest(session_id=session_id, body=body)
    return create_thread_request(session_id, params)


def _get_thread_error_code(error: BaseException) -> Optional[str]:
    """Anthropic 线程拒绝的 ``error.details.error_code``（对齐 pi）。"""
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    inner = body.get("error")
    if not isinstance(inner, dict):
        return None
    details = inner.get("details")
    if not isinstance(details, dict):
        return None
    code = details.get("error_code")
    return code if isinstance(code, str) else None


def _status_of(error: BaseException) -> Optional[int]:
    status = getattr(error, "status_code", None)
    return status if isinstance(status, int) else None


async def send_thread_request(
    prepared: PreparedThreadRequest,
    params: Dict[str, Any],
    send: Callable[[Dict[str, Any]], Awaitable[Any]],
    output: AssistantMessage,
    signal: Optional[AbortSignal],
) -> tuple[Any, Optional[PreparedThreadRequest]]:
    """发送线程请求（对齐 pi sendThreadRequest）：

    线程相关的拒绝（永远发生在流开始前）重发一次——续接失效换新线程
    全量；端点不认 threads 则不带 ``thread``（并把会话关停）。其余错误
    原样上抛。返回 ``(response, 实际生效的线程请求或 None)``。
    """
    try:
        return await send(prepared.body), prepared
    except Exception as error:
        if is_aborted(signal):
            raise
        error_code = _get_thread_error_code(error)
        start_new_thread = error_code is not None and error_code in _NEW_THREAD_ERRORS
        # 端点用不了 threads：带码的 thread_unsupported_request，或无码但
        # 400 且报文提及 thread（如 beta 头缺 threads 时 "thread: Extra
        # inputs are not permitted"）
        threads_unsupported = error_code == "thread_unsupported_request" or (
            error_code is None and _status_of(error) == 400 and "thread" in str(error)
        )
        # 其余错误（prompt too long、未知模型、strict 限量……）与线程无关，
        # 重发只会再失败一次
        if not start_new_thread and not threads_unsupported:
            raise
        # 先记诊断再重发——重发失败也留痕（对齐 pi appendDiagnostic 时序）
        append_diagnostic(
            output,
            {
                "type": "anthropic_thread_fallback",
                "error": {"message": str(error)},
                "details": {"reason": error_code or str(_status_of(error))},
            },
        )
        if start_new_thread:
            created = create_thread_request(prepared.session_id, params)
            return await send(created.body), created
        # 带 threads beta 时，不带 thread 的请求即普通请求
        response = await send(params)
        _set_thread_state(prepared.session_id, "off")
        return response, None


def record_thread_reply(
    model: Model,
    thread: PreparedThreadRequest,
    params: Dict[str, Any],
    output: AssistantMessage,
    compat: ResolvedAnthropicCompat,
    betas: List[str],
    is_oauth: bool = False,
) -> None:
    """记录会话下一请求的续接位置（对齐 pi recordThreadReply）：

    ``output`` 是线程请求 ``thread`` 的干净回复，其完整请求是 ``params``；
    ``betas`` 与发送时的 beta 列表同源（指纹两侧必须同构）。
    """
    # response_model 仅在 fallback 模型应答时落——服务端不存那些回复，无法续接
    if output.response_id is None or output.response_model is not None:
        return
    # 按下一次请求的方式转换回复，只留 assistant 消息（transform 会为
    # 尾部工具调用补占位结果）
    reply = [
        message
        for message in convert_messages(
            model, Context(messages=[output]), compat, None, is_oauth=is_oauth
        ).messages
        if message.get("role") == "assistant"
    ]
    prefix = list(params.get("messages") or []) + reply
    _set_thread_state(
        thread.session_id,
        ThreadEntry(
            prefix_count=len(prefix),
            prefix_hash=hash_thread_prefix(model, params, prefix, betas),
            reply_id=output.response_id,
        ),
    )


__all__ = [
    "MESSAGE_THREADS_BETA",
    "PreparedThreadRequest",
    "ThreadEntry",
    "create_thread_request",
    "hash_thread_prefix",
    "prepare_thread_request",
    "record_thread_reply",
    "send_thread_request",
]
