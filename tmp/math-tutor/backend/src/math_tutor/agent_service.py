"""数学教学 Agent 服务

封装了基于 nova_ai + nova_agent 的出题与问答能力。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Callable, Optional

from nova_agent import Agent
from nova_ai import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    EventStream,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    SimpleStreamOptions,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    ToolCall,
    ToolCallEndEvent,
    ToolResultMessage,
    UserMessage,
    builtin_models,
    create_models,
    get_builtin_model,
    get_volcengine_model,
    volcengine_provider,
)

from . import config


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------
MATH_TUTOR_SYSTEM_PROMPT = """你是一位专业的数学教师，擅长为中小学生讲解数学概念并出具习题。

你的任务包括：
1. 回答学生的数学问题，用简洁、通俗的语言解释。
2. 根据学生的年级/难度需求，生成适当的数学练习题。
3. 每道题目必须附带答案和详细解析。

出题时请遵循以下格式：
- 题目：...
- 答案：...
- 解析：...

如果用户没有明确说明年级或难度，默认生成小学高年级水平的题目。
"""


# ---------------------------------------------------------------------------
# 模型初始化
# ---------------------------------------------------------------------------
def _resolve_model() -> Model:
    """根据配置获取模型实例。"""
    provider_id = config.DEFAULT_PROVIDER
    model_id = config.DEFAULT_MODEL_ID

    # 如果是内置 provider，直接使用 builtin_models
    if provider_id == "volcengine":
        try:
            return get_volcengine_model(model_id)
        except Exception:
            pass

    # 尝试从 builtin_models 中查找
    models = builtin_models()
    model = models.get_model(provider_id, model_id)
    if model is not None:
        return model

    # 如果配置了自定义 base_url，构建一个通用 OpenAI 兼容模型
    base_url = os.environ.get("NOVA_MATH_BASE_URL", "")
    if base_url:
        return Model(
            id=model_id,
            name=model_id,
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=provider_id,
            base_url=base_url,
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=8192,
            max_tokens=4096,
        )

    raise RuntimeError(
        f"无法解析模型: provider={provider_id}, model={model_id}. "
        "请检查 NOVA_MATH_MODEL_PROVIDER / NOVA_MATH_MODEL_ID 环境变量或是否已配置 base_url。"
    )


def _create_models_collection():
    """构建并配置 Models 集合。"""
    models = create_models()

    # 对于内置 provider，注册它的工厂
    if config.DEFAULT_PROVIDER == "volcengine":
        models.set_provider(volcengine_provider())
    elif config.DEFAULT_PROVIDER == "moonshotai":
        from nova_ai import moonshotai_provider

        models.set_provider(moonshotai_provider())
    elif config.DEFAULT_PROVIDER == "kimi_coding":
        from nova_ai import kimi_coding_provider

        models.set_provider(kimi_coding_provider())
    elif config.DEFAULT_PROVIDER == "openai":
        from nova_ai import create_provider

        base_url = os.environ.get("NOVA_MATH_BASE_URL", "https://api.openai.com/v1")
        models.set_provider(
            create_provider(
                id="openai",
                name="OpenAI",
                base_url=base_url,
                api_impl="openai_completions",
            )
        )
    else:
        # 通用 OpenAI 兼容 provider
        from nova_ai import create_provider

        base_url = os.environ.get("NOVA_MATH_BASE_URL")
        if not base_url:
            raise RuntimeError(
                f"非内置 provider {config.DEFAULT_PROVIDER} 需要配置 NOVA_MATH_BASE_URL"
            )
        models.set_provider(
            create_provider(
                id=config.DEFAULT_PROVIDER,
                name=config.DEFAULT_PROVIDER,
                base_url=base_url,
                api_impl="openai_completions",
            )
        )

    return models


# ---------------------------------------------------------------------------
# Agent 实例
# ---------------------------------------------------------------------------
class MathTutorAgent:
    """数学教学 Agent，基于 nova_agent.Agent 封装。"""

    def __init__(self) -> None:
        self._models = _create_models_collection()
        self._model = _resolve_model()
        self._agent = Agent(
            stream_fn=self._models.stream_simple,
        )
        self._agent.set_model(self._model)
        self._agent.set_system_prompt(MATH_TUTOR_SYSTEM_PROMPT)

    async def chat(
        self,
        message: str,
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        """简单问答：接收用户问题，返回流式文本答复。"""
        full_text: list[str] = []

        def listener(event, signal=None):
            from nova_agent import MessageUpdateEvent

            if isinstance(event, MessageUpdateEvent):
                text = self._extract_text(event.message)
                if text:
                    if on_delta:
                        # 计算增量
                        delta = text[len("".join(full_text)) :]
                        if delta:
                            on_delta(delta)
                    full_text.append(text[len("".join(full_text)) :])

        self._agent.subscribe(listener)
        await self._agent.prompt(message)
        await self._agent.wait_for_idle()
        return "".join(full_text)

    async def generate_questions(
        self,
        topic: str,
        count: int = 3,
        difficulty: str = "小学高年级",
        on_delta: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
一键出题：根据知识点、数量和难度生成习题。

        注意：出题是独立任务，不带入之前问答的对话历史。
        """
        # 为出题创建独立 Agent，避免干扰当前对话
        standalone_agent = Agent(stream_fn=self._models.stream_simple)
        standalone_agent.set_model(self._model)
        standalone_agent.set_system_prompt(MATH_TUTOR_SYSTEM_PROMPT)

        prompt = (
            f"请为{difficulty}学生出 {count} 道关于「{topic}」的数学练习题，"
            f"每道题目需附带答案和详细解析。"
        )

        full_text: list[str] = []

        def listener(event, signal=None):
            from nova_agent import MessageUpdateEvent

            if isinstance(event, MessageUpdateEvent):
                text = self._extract_text(event.message)
                if text:
                    if on_delta:
                        delta = text[len("".join(full_text)) :]
                        if delta:
                            on_delta(delta)
                    full_text.append(text[len("".join(full_text)) :])

        standalone_agent.subscribe(listener)
        await standalone_agent.prompt(prompt)
        await standalone_agent.wait_for_idle()
        return "".join(full_text)

    def reset(self) -> None:
        """清空对话历史。"""
        self._agent.reset()

    @staticmethod
    def _extract_text(message: AssistantMessage | None) -> str:
        """从助手消息中提取文本。"""
        if message is None:
            return ""
        parts: list[str] = []
        for block in message.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return "".join(parts)


# ---------------------------------------------------------------------------
# SSE / 流式辅助
# ---------------------------------------------------------------------------
async def stream_chat_response(
    agent: MathTutorAgent,
    message: str,
) -> AsyncIterator[str]:
    """以 SSE 格式流式返回问答结果。"""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_delta(delta: str) -> None:
        queue.put_nowait(delta)

    async def run() -> None:
        try:
            await agent.chat(message, on_delta=on_delta)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(run())
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def stream_question_response(
    agent: MathTutorAgent,
    topic: str,
    count: int,
    difficulty: str,
) -> AsyncIterator[str]:
    """以 SSE 格式流式返回一键出题结果。"""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def on_delta(delta: str) -> None:
        queue.put_nowait(delta)

    async def run() -> None:
        try:
            await agent.generate_questions(
                topic=topic,
                count=count,
                difficulty=difficulty,
                on_delta=on_delta,
            )
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(run())
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
