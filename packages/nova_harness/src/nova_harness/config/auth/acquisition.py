# auth/acquisition.py
"""授权码获取的通道装配与竞速仲裁（Auth 接线层）。

浏览器回调流的"收货"在此组装，三个通道按能力择优：

1. **宿主监听**（``host:listenOnce``，首选）——前端在用户本机起一次性
   监听器，任何部署拓扑（本地/远程）收货点都正确；
2. **本地一次性监听**（进程内裸 TCP 服务器）——无前端能力/headless/
   进程内调用时的兜底；
3. **手动粘贴框**（``request.manual_prompt`` 存在时挂）——自动通道
   静默失败的最终兜底。

竞速仲裁：任一通道先产出**合法**授权码即胜，败者在 finally 中清扫
（取消任务、关服务器、撤宿主监听）——三条取消路径（宿主取消 /
signal 举旗 / 竞速落败）必须全部汇到同一清扫点，漏一条就是幽灵框
或僵尸监听。先完成但未产出合法码的通道（state 不符、端口被占降级）
不结束竞速，其余通道继续。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Dict, Optional, cast
from urllib.parse import parse_qs, urlparse

from nova_harness.types.ui.context import UIContext
from nova_protocol.auth import AuthorizationRequest, AuthPrompt, LoginCancelledError
from nova_protocol.signal import AbortSignal, is_aborted


def _first_qs(parsed: dict, key: str) -> Optional[str]:
    value = parsed.get(key)
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    if isinstance(value, str):
        return value
    return None


def parse_authorization_input(value: str) -> Dict[str, Optional[str]]:
    """解析用户粘贴的授权输入（完整 URL / code#state / 裸 query / 裸 code）。"""
    value = value.strip()
    if not value:
        return {}

    try:
        url = urlparse(value)
        if url.scheme and url.netloc:
            qs = parse_qs(url.query)
            return {"code": _first_qs(qs, "code"), "state": _first_qs(qs, "state")}
    except Exception:
        pass

    if "#" in value:
        code, state = value.split("#", 1)
        return {"code": code, "state": state}

    if "code=" in value:
        qs = parse_qs(value)
        return {"code": _first_qs(qs, "code"), "state": _first_qs(qs, "state")}

    return {"code": value, "state": None}


# ---------------------------------------------------------------------------
# 本地一次性回调服务器（从 nova_ai 迁入——收货归接线层）
# ---------------------------------------------------------------------------


async def _start_local_callback_server(
    *,
    host: str,
    port: int,
    path: str,
    expected_state: str,
    success_html: str,
    error_html: str,
) -> Dict[str, Any]:
    """启动本地一次性 HTTP 服务器接收 OAuth 回调。

    返回 ``{"wait_for_code, close, cancel_wait"}`` 三件操作用于竞速装配。
    state 校验在收货时进行（期望值由请求方给出）——不匹配的回调回 400
    错误页且不结束等待（其余通道仍可兜底）。
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Optional[str]] = loop.create_future()
    server: Optional[asyncio.AbstractServer] = None

    async def _handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request_line = await reader.readline()
            while True:
                line = await reader.readline()
                if line == b"\r\n":
                    break

            request_str = request_line.decode("latin-1")
            parts = request_str.split(" ")
            path_and_query = parts[1] if len(parts) > 1 else "/"
            parsed = urlparse(path_and_query)

            if parsed.path != path:
                html = error_html or "Callback route not found."
                status = "404 Not Found"
            else:
                query = parse_qs(parsed.query)
                got_state = _first_qs(query, "state")
                code = _first_qs(query, "code")

                if got_state != expected_state:
                    html = error_html or "State mismatch."
                    status = "400 Bad Request"
                elif not code:
                    html = error_html or "Missing authorization code."
                    status = "400 Bad Request"
                else:
                    html = success_html or "Authentication completed."
                    status = "200 OK"
                    if not future.done():
                        future.set_result(code)

            body = html.encode("utf-8")
            response = (
                f"HTTP/1.1 {status}\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("latin-1") + body
            writer.write(response)
            await writer.drain()
        except Exception as exc:
            if not future.done():
                future.set_exception(exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    try:
        server = await asyncio.start_server(_handler, host, port)
    except Exception:
        # 端口被占等——收货通道缺席，等待侧返回 None 由装配降级到粘贴框
        future.set_result(None)
        return {
            "wait_for_code": lambda: future,
            "close": lambda: None,
            "cancel_wait": lambda: None if future.done() else future.set_result(None),
        }

    def _close() -> None:
        if server is not None:
            server.close()

    def _cancel_wait() -> None:
        if not future.done():
            future.set_result(None)

    return {
        "wait_for_code": lambda: future,
        "close": _close,
        "cancel_wait": _cancel_wait,
    }


# ---------------------------------------------------------------------------
# 通道装配（竞速仲裁）
# ---------------------------------------------------------------------------


async def acquire_authorization_code(
    ui: UIContext,
    signal: Optional[AbortSignal],
    request: AuthorizationRequest,
    prompt_fn: Callable[[AuthPrompt], Awaitable[str]],
) -> str:
    """装配收货通道并竞速，返回首个合法授权码。

    取消语义三合一：宿主任务取消（cancelRequest → CancelledError 翻译）、
    signal 举旗、竞速落败清扫——全部汇入 finally 清扫点。
    """
    if is_aborted(signal):
        raise LoginCancelledError()

    channels: list[asyncio.Task] = []
    local_server: Optional[Dict[str, Any]] = None

    # 通道 1：宿主监听（前端在用户本机收货——任何拓扑都正确）
    if ui.has_capability("host:listenOnce"):

        async def _via_host() -> Optional[str]:
            resp = await ui.request(
                "host:listenOnce",
                {
                    "port": request.redirect_port,
                    "path": request.redirect_path,
                    "timeoutMs": int(request.timeout_seconds * 1000),
                    "successHtml": request.success_html,
                    "errorHtml": request.error_html,
                },
                scope="global",
            )
            if resp.cancelled:
                return None
            value = resp.value
            if isinstance(value, dict) and value.get("status") == "received":
                # 宿主只原样转发；state 校验在此进行（期望值得自请求）
                if value.get("state") != request.state:
                    return None
                code = value.get("code")
                return code if isinstance(code, str) and code else None
            return None

        channels.append(asyncio.ensure_future(_via_host()))

    # 通道 2：本地一次性监听（进程内兜底——端口被占则内部直接产 None）
    if not ui.has_capability("host:listenOnce"):
        host = os.environ.get("NOVA_OAUTH_CALLBACK_HOST") or "127.0.0.1"
        local_server = await _start_local_callback_server(
            host=host,
            port=request.redirect_port,
            path=request.redirect_path,
            expected_state=request.state,
            success_html=request.success_html,
            error_html=request.error_html,
        )

        async def _via_local() -> Optional[str]:
            return await local_server["wait_for_code"]()

        channels.append(asyncio.ensure_future(_via_local()))

    # 通道 3：手动粘贴框（请求方挂了兜底框才有）
    manual_prompt = request.manual_prompt
    if manual_prompt is not None:

        async def _via_manual() -> Optional[str]:
            answer = await prompt_fn(manual_prompt)  # 取消即上抛
            parsed = parse_authorization_input(answer)
            code = parsed.get("code")
            if not code:
                return None
            # state 校验：粘贴里带了才比（不带视为仅 code——与移植前语义一致）
            pasted_state = parsed.get("state")
            if pasted_state and pasted_state != request.state:
                raise RuntimeError("State mismatch")
            return code

        channels.append(asyncio.ensure_future(_via_manual()))

    async def _wait_channels() -> Optional[str]:
        """等通道产出首个合法码；完成但无产出的通道不终结竞速。"""
        pending = set(channels)
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.cancelled():
                    continue
                result = task.result()  # 通道内异常（如 state 不符）上抛
                if result:
                    return result
        return None

    waiter = asyncio.ensure_future(_wait_channels())

    # signal 举旗监听（有 wait 能力才挂）
    abort_waiter: Optional[asyncio.Task] = None
    signal_wait = cast(
        Optional[Callable[[], Awaitable[None]]],
        getattr(signal, "wait", None) if signal is not None else None,
    )
    if signal_wait is not None:

        async def _watch_abort() -> None:
            await signal_wait()

        abort_waiter = asyncio.ensure_future(_watch_abort())

    try:
        watch_list = [waiter] + ([abort_waiter] if abort_waiter is not None else [])
        done, _pending = await asyncio.wait(
            watch_list,
            timeout=request.timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            waiter.cancel()
            raise TimeoutError("Authorization timed out")
        if abort_waiter is not None and abort_waiter in done:
            waiter.cancel()
            raise LoginCancelledError()
        code = waiter.result()
        if code:
            return code
        raise TimeoutError("Authorization channels exhausted")
    except asyncio.CancelledError:
        # 宿主任务取消（cancelRequest）——翻译为登录取消词汇
        raise LoginCancelledError() from None
    finally:
        waiter.cancel()
        if abort_waiter is not None:
            abort_waiter.cancel()
        for task in channels:
            if not task.done():
                task.cancel()
        if local_server is not None:
            local_server["cancel_wait"]()
            local_server["close"]()


__all__ = [
    "acquire_authorization_code",
    "parse_authorization_input",
]
