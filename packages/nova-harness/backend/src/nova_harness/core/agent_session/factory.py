"""AgentSession 内部工厂函数。

把 ``sdk.py`` 中构造底层 ``Agent``、配置 stream/convert 钩子、
恢复会话状态等细节抽到这里，让 SDK 入口只保留公共工厂 API。
"""

from __future__ import annotations

import inspect
import os
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional

from nova_agent import Agent, ModelThinkingLevel
from nova_ai import ImageContent, Model, ProviderResponse, TextContent
from nova_harness.core.agent_session import AgentSession, AgentSessionConfig
from nova_harness.core.config.defaults import SESSIONS_DIR_NAME
from nova_harness.core.extensions import ExtensionRunner
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.model.attribution import merge_provider_attribution_headers
from nova_harness.core.model.resolver import resolve_thinking_level
from nova_harness.core.types.events import (
    AfterProviderResponseEvent,
    SessionStartEvent,
)
from nova_harness.core.types.events.constants import (
    AFTER_PROVIDER_RESPONSE,
    BEFORE_PROVIDER_HEADERS,
    BEFORE_PROVIDER_REQUEST,
)
from nova_harness.core.types.session.config import CreateAgentSessionOptions
from nova_harness.core.types.session.factory import CreateAgentSessionResult
from nova_harness.core.types.ui import UIContext
from nova_harness.core.utils import resolve_api_key
from nova_harness.core.utils.http_idle_timeout import get_http_idle_timeout_seconds
from nova_harness.core.utils.messages import convert_to_llm

_BLOCK_IMAGE_PLACEHOLDER = "Image reading is disabled."


def resolve_session_manager(
    options: CreateAgentSessionOptions,
    services: Any,
) -> SessionManager:
    """解析或创建 SessionManager。"""
    if options.session_manager is not None:
        return options.session_manager

    agent_dir = Path(services.agent_dir)
    cwd = services.cwd
    cleaned_cwd = cwd.lstrip("/\\").replace("/", "-").replace("\\", "-")
    safe_path = f"--{cleaned_cwd}--"
    session_dir = os.path.join(agent_dir, SESSIONS_DIR_NAME, safe_path)
    os.makedirs(session_dir, exist_ok=True)
    return SessionManager.create(cwd, session_dir)


def configure_extension_runner(session: AgentSession, ui_context: UIContext) -> None:
    """把 UIContext 绑定到扩展 runner。"""
    runner = session.extension_runner
    if runner is None:
        return
    runner.ui_context = ui_context


def resolve_initial_active_tool_names(
    options: CreateAgentSessionOptions,
) -> Optional[List[str]]:
    """计算初始激活工具名的三态：

    - ``None``：未指定——由 ToolsManager 默认（激活注册表全部）；
    - ``[]``：显式不激活（``tools=[]``）；
    - ``[names]``：显式激活集合。

    框架零内置工具、零预设名单——默认分支不提供任何名字。
    """
    if options.tools is not None:
        excluded_set = set(options.exclude_tools or [])
        return [name for name in options.tools if name not in excluded_set]

    return None


def create_stream_fn(
    services: Any,
    session_id: Optional[str],
    extension_runner_ref: Optional[Dict[str, Optional[ExtensionRunner]]] = None,
) -> Any:
    """构造合并 provider attribution headers、重试与超时的 stream_fn。

    每次请求都会重新读取 ``settings_manager`` 中的 retry / http idle timeout
    配置，确保运行中修改 settings 后下一次请求立即生效。
    ``extension_runner_ref`` 提供时挂接 ``before_provider_headers`` 扩展钩子
    （经 ``transform_headers`` 在 provider 派发前原地改写请求头，对齐 pi）。
    """

    async def _stream_fn(model: Model, context: Any, options: Any) -> Any:
        from nova_ai.types.stream_options import SimpleStreamOptions

        if options is None:
            options = SimpleStreamOptions()

        retry_settings = services.settings_manager.get_retry_settings()
        provider_retry = retry_settings.provider

        # Provider 级超时优先于 Agent 默认值和全局 http idle timeout
        provider_timeout_ms = provider_retry.timeout_ms if provider_retry else None
        if provider_timeout_ms is not None and provider_timeout_ms > 0:
            options.timeout = provider_timeout_ms / 1000.0
        elif options.timeout is None:
            options.timeout = get_http_idle_timeout_seconds(services.settings_manager)

        # Provider 级最大重试次数
        if options.max_retries is None:
            provider_max_retries = (
                provider_retry.max_retries if provider_retry else None
            )
            if provider_max_retries is not None:
                options.max_retries = provider_max_retries

        attribution_headers = merge_provider_attribution_headers(
            model,
            services.settings_manager,
            session_id,
            model.headers,
            options.headers,
        )
        if attribution_headers:
            merged = dict(options.headers or {})
            merged.update(attribution_headers)
            options.headers = merged

        if extension_runner_ref is not None:
            options.transform_headers = _chain_transform_headers(
                options.transform_headers, extension_runner_ref
            )
        return services.model_runtime.stream_simple(model, context, options)

    return _stream_fn


def _chain_transform_headers(
    previous: Any,
    extension_runner_ref: Dict[str, Optional[ExtensionRunner]],
) -> Any:
    """把扩展的 headers 钩子链接到既有 transform_headers 之后（若有）。"""

    async def _transform(headers: Any) -> Any:
        if previous is not None:
            transformed = previous(headers)
            if inspect.isawaitable(transformed):
                transformed = await transformed
            headers = transformed
        runner = extension_runner_ref.get("current")
        if runner is None or not runner.has_handlers(BEFORE_PROVIDER_HEADERS):
            return headers
        return await runner.emit_before_provider_headers(headers)

    return _transform


def create_convert_to_llm(settings_manager: Any) -> Any:
    """构造支持 ``blockImages`` 设置的 convert_to_llm 包装器。

    当 ``images.block_images`` 为 ``True`` 时，把 user/toolResult 消息中的
    ``ImageContent`` 替换为占位文本，避免图片被发送到 LLM provider。
    返回的新消息不会修改原始对象。
    """

    async def _convert_to_llm_with_block_images(messages: List[Any]) -> List[Any]:
        converted = convert_to_llm(messages)
        if not settings_manager.get_block_images():
            return converted

        result: List[Any] = []
        for msg in converted:
            if msg.role not in ("user", "toolResult"):
                result.append(msg)
                continue

            content = msg.content
            if isinstance(content, str) or not isinstance(content, list):
                result.append(msg)
                continue

            filtered: List[Any] = []
            for block in content:
                if isinstance(block, ImageContent):
                    filtered.append(
                        TextContent(type="text", text=_BLOCK_IMAGE_PLACEHOLDER)
                    )
                else:
                    filtered.append(block)

            # 去除连续的重复占位文本
            deduped: List[Any] = []
            for block in filtered:
                if (
                    isinstance(block, TextContent)
                    and block.text == _BLOCK_IMAGE_PLACEHOLDER
                    and deduped
                    and isinstance(deduped[-1], TextContent)
                    and deduped[-1].text == _BLOCK_IMAGE_PLACEHOLDER
                ):
                    continue
                deduped.append(block)

            new_content = deduped if deduped else [TextContent(type="text", text="")]
            result.append(msg.model_copy(update={"content": new_content}))

        return result

    return _convert_to_llm_with_block_images


def create_agent(
    services: Any,
    session_manager: SessionManager,
    model: Optional[Model],
    thinking_level: Optional[ModelThinkingLevel],
    extension_runner_ref: Dict[str, Optional[ExtensionRunner]],
) -> Agent:
    """构造底层 Agent，并挂接扩展 runner 的 provider 拦截钩子。"""
    get_api_key = partial(resolve_api_key, model_runtime=services.model_runtime)
    session_id = session_manager.get_session_id()

    return Agent(
        initial_state={
            "system_prompt": None,
            "model": model,
            "thinking_level": thinking_level,
            "tools": [],
        },
        convert_to_llm=create_convert_to_llm(services.settings_manager),
        steering_mode=services.settings_manager.get_steering_mode(),
        follow_up_mode=services.settings_manager.get_follow_up_mode(),
        session_id=session_id,
        get_api_key=get_api_key,
        thinking_budgets=services.settings_manager.get_thinking_budgets(),
        timeout=get_http_idle_timeout_seconds(services.settings_manager),
        stream_fn=create_stream_fn(services, session_id, extension_runner_ref),
        on_payload=lambda payload, model: _on_payload(extension_runner_ref, payload),
        on_response=lambda response, _model: _on_response(
            extension_runner_ref, response, _model
        ),
        transform_context=lambda messages, signal=None: _transform_context(
            extension_runner_ref, messages, signal
        ),
    )


async def _on_payload(
    extension_runner_ref: Dict[str, Optional[ExtensionRunner]],
    payload: Any,
) -> Any:
    runner = extension_runner_ref.get("current")
    if runner is None or not runner.has_handlers(BEFORE_PROVIDER_REQUEST):
        return payload
    return await runner.emit_before_provider_request(payload)


async def _on_response(
    extension_runner_ref: Dict[str, Optional[ExtensionRunner]],
    response: ProviderResponse,
    _model: Any,
) -> None:
    runner = extension_runner_ref.get("current")
    if runner is None or not runner.has_handlers(AFTER_PROVIDER_RESPONSE):
        return
    await runner.emit(
        AfterProviderResponseEvent(
            status=response.status,
            headers=response.headers,
            model=_model,
        )
    )


async def _transform_context(
    extension_runner_ref: Dict[str, Optional[ExtensionRunner]],
    messages: List[Any],
    signal: Optional[Any] = None,
) -> List[Any]:
    runner = extension_runner_ref.get("current")
    if runner is None:
        return messages
    return await runner.emit_context(messages, signal)


def restore_or_persist_session_state(
    session_manager: SessionManager,
    agent: Agent,
    model: Optional[Model],
    thinking_level: Optional[ModelThinkingLevel],
) -> None:
    """恢复历史消息，或为新的会话写入初始 model/thinking entry。"""
    existing_session = session_manager.build_session_context()
    has_existing_session = len(existing_session.messages) > 0
    has_thinking_entry = any(
        e.type == "thinking_level_change" for e in session_manager.get_branch()
    )

    if has_existing_session:
        agent.state.messages = existing_session.messages
        if not has_thinking_entry and thinking_level is not None:
            session_manager.append_thinking_level_change(thinking_level)
        return

    if model is not None:
        session_manager.append_model_change(model.provider, model.id)
    if thinking_level is not None:
        session_manager.append_thinking_level_change(thinking_level)


def build_agent_session_config(
    services: Any,
    session_manager: SessionManager,
    agent: Agent,
    options: CreateAgentSessionOptions,
    initial_active_tool_names: Optional[List[str]],
    extension_runner_ref: Dict[str, Optional[ExtensionRunner]],
) -> AgentSessionConfig:
    """从服务集合与已创建的 Agent 构造 AgentSessionConfig。"""
    return AgentSessionConfig(
        agent=agent,
        session_manager=session_manager,
        settings_manager=services.settings_manager,
        cwd=services.cwd,
        resource_loader=services.resource_loader,
        model_runtime=services.model_runtime,
        agent_name=options.agent_name,
        agent_dir=services.agent_dir,
        scoped_models=options.scoped_models or [],
        initial_active_tool_names=initial_active_tool_names,
        base_tools_override=options.base_tools_override,
        custom_tools=options.custom_tools,
        allowed_tool_names=options.tools,
        excluded_tool_names=options.exclude_tools,
        extension_runner_ref=extension_runner_ref,
        session_start_event=SessionStartEvent(reason="new"),
    )


__all__ = [
    "configure_extension_runner",
    "create_agent",
    "create_convert_to_llm",
    "create_stream_fn",
    "resolve_initial_active_tool_names",
    "resolve_session_manager",
    "restore_or_persist_session_state",
    "build_agent_session_config",
]
