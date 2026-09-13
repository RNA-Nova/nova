"""OAuth HTTP 共享件：可被 AbortSignal 取消的 POST。

httpx（与 openai SDK 同病）不支持请求级 signal——本模块把"可中断的 POST"
做成原语：watchdog 竞速，举旗即取消在飞请求，不等超时兜底。
kimi 与 openai_codex 两个流程实现共用（传输错误的包装口径也在这里统一）。
"""

import asyncio
from typing import Any, Dict, Optional

import httpx
from nova_protocol import AbortSignal

__all__ = ["post_with_abort"]


async def post_with_abort(
    url: str,
    *,
    data: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 30.0,
    signal: Optional[AbortSignal] = None,
) -> httpx.Response:
    """发送 POST；``signal`` 中断即取消在飞请求并抛 ``asyncio.CancelledError``。

    传输错误（连接失败/超时等）统一包装为 ``RuntimeError``——调用方按
    RuntimeError 识别传输失败并重试，无需感知 httpx 异常族。
    """

    async def _do_post() -> httpx.Response:
        try:
            async with httpx.AsyncClient() as client:
                return await client.post(
                    url,
                    data=data,
                    json=json_body,
                    headers=headers,
                    timeout=timeout,
                )
        except httpx.TransportError as error:
            raise RuntimeError(f"OAuth request to {url} failed: {error}") from error

    post_task = asyncio.ensure_future(_do_post())
    if signal is None:
        return await post_task

    watcher = asyncio.ensure_future(signal.wait())
    try:
        done, _pending = await asyncio.wait(
            [post_task, watcher], return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in done and post_task not in done:
            post_task.cancel()
            raise asyncio.CancelledError("Request aborted")
        return post_task.result()
    finally:
        watcher.cancel()
        if not post_task.done():
            post_task.cancel()
