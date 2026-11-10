"""假 LLM 服务器（对位 codex wiremock + ResponseMock——假在传输，真在协议）。

两件套：

- ``RequestCapture``：出站请求捕获——记录全部请求，带"恰好一次"守卫
  （``single()``）与请求体断言入口。LLM agent 最值钱的回归面是"发给
  模型的请求体"，本件把它变成一等断言对象（codex ``single_request()``
  对位）；
- SSE 事件构造器 + 传输挂载：openai 经 respx（SDK 的 httpx 底座被
  respx 全局拦截，客户端构造与 params 序列化全真跑）、anthropic 经
  httpx2.MockTransport 注入（httpx2 命名分叉 respx 拦不住，客户端经
  options.client 注入）。

场景覆盖：正常文本/工具调用流、错误状态（4xx/5xx）、流中途断裂
（无 finish_reason / 传输错误）——真实 API 无法定向复现的场景全在这。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx
import httpx2
import respx
from anthropic import AsyncAnthropic


class RequestCapture:
    """出站请求捕获（恰好一次守卫 + 请求体断言入口）。"""

    def __init__(self) -> None:
        self.requests: List[Any] = []

    def record(self, request: Any) -> None:
        self.requests.append(request)

    def __len__(self) -> int:
        return len(self.requests)

    def single(self) -> Any:
        """恰好一次请求（≠1 直接失败——抓意外多发/重试 bug）。"""
        if len(self.requests) != 1:
            raise AssertionError(
                f"expected exactly one request, got {len(self.requests)}"
            )
        return self.requests[0]

    def at(self, index: int) -> Any:
        return self.requests[index]

    @staticmethod
    def body_json(request: Any) -> Any:
        """请求体 JSON（即"发给模型的完整参数包"）。"""
        return json.loads(request.content)


# ---------------------------------------------------------------------------
# 受控字节流（挂起/断裂/精确错误帧——真实 API 无法定向复现的场景）
# ---------------------------------------------------------------------------


class _ScriptedStreamBase:
    """受控字节流编排：逐载荷交付后按编排收尾。

    - ``hang=True``：载荷耗尽后挂起，直到 ``aclose`` 放行（对应真实连接被
      关闭时读端解锁；服务端半死/长沉默场景）；
    - ``error``：载荷耗尽后抛该异常（传输断裂 / 精确错误帧直达）。

    编排观测口（§七 Event 受控完成序，不用 sleep 表达等待）：

    - ``delivered``：首个载荷交付后置位（确认流已在飞再举旗）；
    - ``waiting``：进入挂起等待前置位（确认消费者已阻塞在读上）；
    - ``closed``：``aclose`` 后置位（"流被关闭"的断言出口——SDK 的
      ``stream.close()`` 沿 response 一路到这里，全真跑）。
    """

    def __init__(
        self,
        payloads: List[bytes],
        *,
        hang: bool = False,
        error: Optional[Exception] = None,
    ) -> None:
        self._payloads = payloads
        self._hang = hang
        self._error = error
        self.delivered = asyncio.Event()
        self.waiting = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for payload in self._payloads:
            yield payload
            self.delivered.set()
        if self._error is not None:
            raise self._error
        if self._hang:
            self.waiting.set()
            await self.closed.wait()

    async def aclose(self) -> None:
        self.closed.set()


class OpenAIScriptedStream(_ScriptedStreamBase, httpx.AsyncByteStream):
    """httpx 底座的受控流（openai SDK——经 respx 过线）。"""


class AnthropicScriptedStream(_ScriptedStreamBase, httpx2.AsyncByteStream):
    """httpx2 底座的受控流（anthropic SDK——经 MockTransport 注入）。"""


# ---------------------------------------------------------------------------
# OpenAI chat.completions 方言（respx 挂载）
# ---------------------------------------------------------------------------


def oai_chunk(
    *,
    content: Optional[str] = None,
    reasoning: Optional[str] = None,
    reasoning_details: Optional[List[Dict[str, Any]]] = None,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    finish: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    model: str = "test-model",
) -> Dict[str, Any]:
    """构造一个 chat.completion.chunk。"""
    delta: Dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    if reasoning_details is not None:
        delta["reasoning_details"] = reasoning_details
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    chunk: Dict[str, Any] = {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def oai_tool_call(
    index: Optional[int] = 0,
    *,
    id: Optional[str] = None,
    name: Optional[str] = None,
    arguments: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 chunk 的 tool_calls delta 项（分片到达时逐字段一个 delta）。"""
    item: Dict[str, Any] = {}
    if index is not None:
        item["index"] = index
    if id is not None:
        item["id"] = id
    function: Dict[str, Any] = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    if function:
        item["function"] = function
    return item


def _oai_sse(chunks: List[Dict[str, Any]]) -> bytes:
    lines = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
    return (lines + "data: [DONE]\n\n").encode()


def mount_openai_chat(
    chunks: List[Dict[str, Any]],
    capture: RequestCapture,
    *,
    status: int = 200,
    error_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> None:
    """respx 挂载 chat completions 端点（请求进 capture）。

    需在 ``@respx.mock`` 作用内调用；``status != 200`` 时返回 JSON 错误体
    （缺省通用 provider 错误形状）。``headers`` 并入响应头（成功路径自动
    带 content-type: text/event-stream）——错误头（retry-after 家族）与
    凭据类头（set-cookie 等过滤断言）场景用。
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        capture.record(request)
        if status == 200:
            return httpx.Response(
                200,
                content=_oai_sse(chunks),
                headers={"content-type": "text/event-stream", **(headers or {})},
            )
        return httpx.Response(
            status,
            json=error_body
            or {
                "error": {
                    "message": f"fake provider error {status}",
                    "type": "server_error",
                }
            },
            headers=headers,
        )

    respx.post(url__regex=r".*/chat/completions$").mock(side_effect=_handler)


def mount_openai_transport_error(error: Exception, capture: RequestCapture) -> None:
    """挂载一个直接抛传输错误的端点（连接失败/超时场景）。"""

    def _handler(request: httpx.Request) -> httpx.Response:
        capture.record(request)
        raise error

    respx.post(url__regex=r".*/chat/completions$").mock(side_effect=_handler)


def oai_scripted_stream(
    chunks: List[Dict[str, Any]],
    *,
    hang: bool = False,
    error: Optional[Exception] = None,
) -> OpenAIScriptedStream:
    """chunk 序列 → 受控字节流（每 chunk 一个 SSE 帧；不带 [DONE]——收尾归编排）。"""
    payloads = [f"data: {json.dumps(c)}\n\n".encode() for c in chunks]
    return OpenAIScriptedStream(payloads, hang=hang, error=error)


def mount_openai_stream(stream: OpenAIScriptedStream, capture: RequestCapture) -> None:
    """respx 挂载受控流端点（挂起/断裂编排；请求进 capture）。

    需在 ``@respx.mock`` 作用内调用。
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        capture.record(request)
        return httpx.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    respx.post(url__regex=r".*/chat/completions$").mock(side_effect=_handler)


def mount_openai_hanging_request(
    capture: RequestCapture,
) -> Tuple[asyncio.Event, asyncio.Event]:
    """挂载响应头永不到达的端点（connect→TTFB 盲区治理场景）。

    返回 ``(started, killed)``：handler 进点置 ``started``（测试确认请求已
    在飞再举旗）；被取消时亲见 ``CancelledError`` 置 ``killed``（scope 真杀
    的观测口）。需在 ``@respx.mock`` 作用内调用。
    """
    started = asyncio.Event()
    killed = asyncio.Event()

    async def _handler(request: httpx.Request) -> httpx.Response:
        capture.record(request)
        started.set()
        try:
            await asyncio.Event().wait()  # 永不放行
        except asyncio.CancelledError:
            killed.set()
            raise
        raise AssertionError("真杀语义下 handler 不会等到放行")  # pragma: no cover

    respx.post(url__regex=r".*/chat/completions$").mock(side_effect=_handler)
    return started, killed


# ---------------------------------------------------------------------------
# Anthropic messages 方言（httpx2.MockTransport 注入）
# ---------------------------------------------------------------------------

AntFrame = Tuple[str, Dict[str, Any]]
"""一个 SSE 帧（event 名, data 载荷）。"""


def ant_message_start(
    model: str = "claude-x",
    input_tokens: int = 12,
    *,
    id: str = "msg_fake",
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    ephemeral_1h_input_tokens: int = 0,
) -> AntFrame:
    usage: Dict[str, Any] = {"input_tokens": input_tokens, "output_tokens": 1}
    if cache_read_input_tokens:
        usage["cache_read_input_tokens"] = cache_read_input_tokens
    if cache_creation_input_tokens:
        usage["cache_creation_input_tokens"] = cache_creation_input_tokens
    if ephemeral_1h_input_tokens:
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": ephemeral_1h_input_tokens
        }
    return (
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "usage": usage,
            },
        },
    )


def ant_block_start(index: int, block: Dict[str, Any]) -> AntFrame:
    return (
        "content_block_start",
        {"type": "content_block_start", "index": index, "content_block": block},
    )


def ant_text_block(index: int = 0) -> AntFrame:
    return ant_block_start(index, {"type": "text", "text": ""})


def ant_text_delta(text: str, index: int = 0) -> AntFrame:
    return (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
    )


def ant_thinking_block(
    index: int = 0, *, thinking: str = "", signature: str = ""
) -> AntFrame:
    return ant_block_start(
        index, {"type": "thinking", "thinking": thinking, "signature": signature}
    )


def ant_thinking_delta(text: str, index: int = 0) -> AntFrame:
    return (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "thinking_delta", "thinking": text},
        },
    )


def ant_signature_delta(signature: str, index: int = 0) -> AntFrame:
    return (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "signature_delta", "signature": signature},
        },
    )


def ant_redacted_thinking_block(index: int = 0, *, data: str) -> AntFrame:
    return ant_block_start(index, {"type": "redacted_thinking", "data": data})


def ant_tool_use_block(
    index: int, *, id: str, name: str, input: Optional[Dict[str, Any]] = None
) -> AntFrame:
    return ant_block_start(
        index,
        {
            "type": "tool_use",
            "id": id,
            "name": name,
            "input": input if input is not None else {},
        },
    )


def ant_input_json_delta(partial_json: str, index: int = 0) -> AntFrame:
    return (
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "input_json_delta", "partial_json": partial_json},
        },
    )


def ant_block_stop(index: int = 0) -> AntFrame:
    return ("content_block_stop", {"type": "content_block_stop", "index": index})


def ant_message_delta(
    stop_reason: str = "end_turn",
    output_tokens: int = 5,
    *,
    stop_details: Optional[Dict[str, Any]] = None,
) -> AntFrame:
    delta: Dict[str, Any] = {"stop_reason": stop_reason}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    return (
        "message_delta",
        {
            "type": "message_delta",
            "delta": delta,
            "usage": {"output_tokens": output_tokens},
        },
    )


def ant_message_stop() -> AntFrame:
    return ("message_stop", {"type": "message_stop"})


def ant_error(error_type: str, message: str) -> AntFrame:
    return (
        "error",
        {"type": "error", "error": {"type": error_type, "message": message}},
    )


def ant_text_turn(text: str, *, model: str = "claude-x") -> List[AntFrame]:
    """一个完整文本 turn 的帧序列。"""
    return [
        ant_message_start(model),
        ant_text_block(0),
        ant_text_delta(text, 0),
        ant_block_stop(0),
        ant_message_delta("end_turn"),
        ant_message_stop(),
    ]


def ant_tool_use_turn(*, id: str, name: str, arguments: str) -> List[AntFrame]:
    """一个完整 tool_use turn 的帧序列。"""
    return [
        ant_message_start(),
        ant_tool_use_block(0, id=id, name=name),
        ant_input_json_delta(arguments, 0),
        ant_block_stop(0),
        ant_message_delta("tool_use"),
        ant_message_stop(),
    ]


def _ant_sse(frames: List[AntFrame]) -> bytes:
    return "".join(
        f"event: {event}\ndata: {json.dumps(data)}\n\n" for event, data in frames
    ).encode()


def ant_sse_response(
    frames: List[AntFrame], *, headers: Optional[Dict[str, str]] = None
) -> httpx2.Response:
    """200 SSE 响应（剧本化序列 / 响应头断言场景用）。"""
    return httpx2.Response(
        200,
        content=_ant_sse(frames),
        headers={"content-type": "text/event-stream", **(headers or {})},
    )


def ant_error_response(
    status: int,
    error_body: Optional[Dict[str, Any]] = None,
    *,
    headers: Optional[Dict[str, str]] = None,
) -> httpx2.Response:
    """非 200 错误响应（缺省通用 provider 错误形状）。"""
    return httpx2.Response(
        status,
        json=error_body
        or {
            "type": "error",
            "error": {"type": "api_error", "message": f"fake {status}"},
        },
        headers=headers,
    )


def _anthropic_client_with_handler(handler: Any) -> AsyncAnthropic:
    """注入 MockTransport handler 的 AsyncAnthropic（max_retries=0 纪律）。"""
    return AsyncAnthropic(
        api_key="sk-test",
        base_url="https://api.anthropic.com",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )


def anthropic_client(
    frames: List[AntFrame],
    capture: RequestCapture,
    *,
    status: int = 200,
    error_body: Optional[Dict[str, Any]] = None,
    response_headers: Optional[Dict[str, str]] = None,
) -> AsyncAnthropic:
    """注入 MockTransport 的 AsyncAnthropic（max_retries=0 纪律）。

    ``response_headers`` 并入成功路径响应头（凭据类头过滤断言等场景）。
    """

    def _handler(request: httpx2.Request) -> httpx2.Response:
        capture.record(request)
        if status == 200:
            return ant_sse_response(frames, headers=response_headers)
        return ant_error_response(status, error_body, headers=response_headers)

    return _anthropic_client_with_handler(_handler)


def anthropic_client_scripted(
    responses: List[httpx2.Response], capture: RequestCapture
) -> AsyncAnthropic:
    """剧本化响应序列的 AsyncAnthropic（每请求消费一个——重试编排场景）。

    请求次数超过编排长度即AssertionError（抓意外多发/重试 bug，与
    ``RequestCapture.single`` 同族守卫）。
    """
    queue = list(responses)

    def _handler(request: httpx2.Request) -> httpx2.Response:
        capture.record(request)
        if not queue:
            raise AssertionError("剧本耗尽：请求次数超过编排响应数")
        return queue.pop(0)

    return _anthropic_client_with_handler(_handler)


def ant_scripted_stream(
    frames: List[AntFrame],
    *,
    hang: bool = False,
    error: Optional[Exception] = None,
) -> AnthropicScriptedStream:
    """帧序列 → 受控字节流（每帧一个 SSE event；收尾归编排——挂起/抛错）。"""
    payloads = [
        f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()
        for event, data in frames
    ]
    return AnthropicScriptedStream(payloads, hang=hang, error=error)


def anthropic_stream_client(
    stream: AnthropicScriptedStream, capture: RequestCapture
) -> AsyncAnthropic:
    """注入受控字节流的 AsyncAnthropic（挂起/断裂编排；请求进 capture）。"""

    def _handler(request: httpx2.Request) -> httpx2.Response:
        capture.record(request)
        return httpx2.Response(
            200,
            stream=stream,
            headers={"content-type": "text/event-stream"},
        )

    return _anthropic_client_with_handler(_handler)
