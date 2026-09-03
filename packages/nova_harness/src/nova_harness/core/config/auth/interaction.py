# auth/interaction.py
"""把 OAuth/API key 登录流程的 ``AuthInteraction`` 桥接到 ``UIContext``。

对齐 TS ``interactive-mode.ts`` 的 ``showAuthPrompt`` / ``notifyAuthDialog``：

- prompt ``select`` → 选择器（label 展示，返回 option id）
- prompt 其他类型（secret/text/manual_code）→ 文本输入框
- 取消、前端不支持、或 signal abort → 抛 ``LoginCancelledError``
- notify ``auth_url`` / ``device_code`` / ``info`` / ``progress`` → 通知与状态
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from nova_ai.signal import AbortSignal
from nova_ai.types.auth import AuthEvent, AuthInteraction, AuthPrompt
from nova_harness.core.types.ui.context import UIContext


class LoginCancelledError(Exception):
    """登录流程被用户取消或被 signal 中止。"""

    def __init__(self) -> None:
        super().__init__("Login cancelled")


class UIAuthInteraction(AuthInteraction):
    """基于 UIContext 的登录交互实现。"""

    def __init__(self, ui: UIContext, signal: Optional[AbortSignal] = None) -> None:
        self.ui = ui
        self.signal = signal
        self._opened_url: Optional[str] = None

    def _open_browser(self, url: Optional[str]) -> None:
        """自动打开浏览器进入授权页（gh/vercel/Claude Code 同款 UX）。

        同一 URL 每流程只开一次（device_code 与 auth_url 可能相继携带同一
        地址）；失败静默（无浏览器环境——URL 仍在等待框里可点）。
        """
        if not url or url == self._opened_url:
            return
        self._opened_url = url
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass

    async def prompt(self, prompt: AuthPrompt) -> str:
        signal = prompt.signal or self.signal
        if signal is not None and signal.aborted:
            raise LoginCancelledError()

        if prompt.type == "select" and prompt.options:
            coro = self._prompt_select(prompt, signal)
        else:
            coro = self._prompt_input(prompt, signal)

        # request 内部已做 signal 竞速（abort → ui/cancel 撤销 + cancelled 返回）
        result = await coro

        if result is None:
            raise LoginCancelledError()
        return result

    def notify(self, event: AuthEvent) -> None:
        if event.type == "auth_url":
            self._open_browser(event.url)
            parts = [event.url or ""]
            if event.instructions:
                parts.append(event.instructions)
            self._notify_auth(
                "\n".join(p for p in parts if p),
                url=event.url,
            )
        elif event.type == "device_code":
            self._open_browser(event.verificationUriComplete or event.verificationUri)
            lines = []
            if event.verificationUri:
                lines.append(f"Open: {event.verificationUri}")
            if event.verificationUriComplete:
                lines.append(f"Or open directly: {event.verificationUriComplete}")
            if event.userCode:
                lines.append(f"Code: {event.userCode}")
            if event.expiresInSeconds:
                lines.append(f"Expires in {int(event.expiresInSeconds / 60)} minutes")
            self._notify_auth(
                "\n".join(lines),
                url=event.verificationUriComplete or event.verificationUri,
                userCode=event.userCode,
            )
            # 注意：不再另发 "Waiting for authentication..." 的 progress 通知——
            # 授权等待框（type="auth" → AuthWaitingDialog）自带等待文案，
            # 再发一条会常驻状态行（轮询期每次事件重发，清除后又被刷回）。
        elif event.type == "info":
            message = event.message or ""
            if event.links:
                message += "\n" + "\n".join(
                    f"{link.label or link.url}: {link.url}" for link in event.links
                )
            self._notify(message, "info")
        else:
            # progress 及其他事件一律按进度提示（前端自行决定呈现方式）
            if event.message:
                self._notify(event.message, "progress")

    def _notify(self, message: str, type: str = "info") -> None:
        """经泛型 notify 通道发送通知（词汇 "notify" 由官方 bundle 定义）。"""
        self.ui.notify("notify", {"message": message, "type": type})

    def _notify_auth(self, message: str, **fields: Any) -> None:
        """授权等待通知：``type="auth"`` 标记 + 结构化字段（url/userCode）。

        前端据此开授权等待框（Esc → cancelRequest 取消登录调用）；
        message 保留人类可读文本——不识别 auth 类型的消费者按普通通知
        显示文本（headless/日志兜底）。
        """
        self.ui.notify("notify", {"message": message, "type": "auth", **fields})

    async def _prompt_select(
        self, prompt: AuthPrompt, signal: Optional[AbortSignal]
    ) -> Optional[str]:
        options = prompt.options or []
        labels = [option.label for option in options]
        params: Dict[str, Any] = {"title": prompt.message, "options": labels}
        if prompt.placeholder is not None:
            params["placeholder"] = prompt.placeholder
        resp = await self.ui.request("select", params, signal)
        if resp.cancelled or not isinstance(resp.value, str):
            return None
        for option in options:
            if option.label == resp.value:
                return option.id
        return None

    async def _prompt_input(
        self, prompt: AuthPrompt, signal: Optional[AbortSignal]
    ) -> Optional[str]:
        params: Dict[str, Any] = {"title": prompt.message}
        if prompt.placeholder is not None:
            params["placeholder"] = prompt.placeholder
        resp = await self.ui.request("input", params, signal)
        if resp.cancelled or not isinstance(resp.value, str):
            return None
        return resp.value


__all__ = ["LoginCancelledError", "UIAuthInteraction"]
