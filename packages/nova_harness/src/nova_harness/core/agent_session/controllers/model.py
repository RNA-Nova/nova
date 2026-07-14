"""模型与思考级别控制。"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from nova_ai import Model, ThinkingLevel

from nova_harness.core.types.agent.model import ModelCycleResult
from nova_harness.core.types.events import (
    ModelSelectEvent,
    ThinkingLevelChangedEvent,
    ThinkingLevelSelectEvent,
)
from nova_harness.core.types.protocols import AgentSessionProtocol
from nova_harness.core.utils import models_are_equal


def _thinking_level_from_value(
    value: Optional[object],
) -> Optional[ThinkingLevel]:
    """把字符串或枚举映射为 ThinkingLevel；``"none"`` 与 ``None`` 都表示关闭。"""
    if value is None:
        return None
    if isinstance(value, ThinkingLevel):
        return value
    text = str(value).lower()
    if text in ("none", "off", ""):
        return None
    try:
        return ThinkingLevel(text)
    except ValueError:
        return ThinkingLevel.MEDIUM


class ModelController:
    """封装 AgentSession 的模型切换、思考级别与相关事件。"""

    def __init__(self, session: AgentSessionProtocol) -> None:
        self._session = session

    async def emit_model_select(
        self,
        next_model: Model,
        previous_model: Optional[Model],
        source: str,
    ) -> None:
        """向扩展发射 model_select 事件。"""
        if models_are_equal(previous_model, next_model):
            return
        runner = self._session._extension_runner
        if runner is not None:
            await runner.emit_model_select(
                ModelSelectEvent(
                    model=next_model, previous_model=previous_model, source=source
                )
            )

    async def set_model(self, model: Model) -> bool:
        """设置当前模型并持久化到会话与设置。

        返回 ``True`` 表示切换成功；若缺少 API key 则返回 ``False``。
        """
        registry = self._session.model_registry
        api_key = await registry.get_api_key(model)
        if not api_key:
            return False

        previous_model = self._session.model
        thinking_level = self._get_thinking_level_for_model_switch()
        self._session.agent.state.model = model
        self._session.session_manager.append_model_change(model.provider, model.id)
        settings = self._session.settings_manager
        settings.set_default_model_and_provider(model.provider, model.id)

        # 根据新模型能力重新钳制思考级别
        await self.set_thinking_level(thinking_level)

        if previous_model != model:
            await self.emit_model_select(model, previous_model, "set")
        return True

    async def cycle_model(
        self, direction: str = "forward"
    ) -> Optional[ModelCycleResult]:
        """循环切换到下一个/上一个模型。优先使用 scoped_models，否则使用所有可用模型。"""
        if self._session.scoped_models:
            return await self._cycle_scoped_model(direction)
        return await self._cycle_available_model(direction)

    async def _cycle_scoped_model(self, direction: str) -> Optional[ModelCycleResult]:
        scoped = [
            s for s in self._session.scoped_models
            if await self._model_has_auth(s.model)
        ]
        if len(scoped) <= 1:
            return None

        current = self._session.model
        current_index = next(
            (i for i, s in enumerate(scoped) if models_are_equal(s.model, current)),
            -1,
        )
        if current_index == -1:
            current_index = 0
        length = len(scoped)
        next_index = (
            (current_index + 1) % length
            if direction == "forward"
            else (current_index - 1 + length) % length
        )
        nxt = scoped[next_index]
        thinking_level = self._get_thinking_level_for_model_switch(nxt.thinking_level)

        previous = self._session.model
        self._session.agent.state.model = nxt.model
        self._session.session_manager.append_model_change(
            nxt.model.provider, nxt.model.id
        )
        settings = self._session.settings_manager
        settings.set_default_model_and_provider(nxt.model.provider, nxt.model.id)

        if previous != nxt.model:
            await self.set_thinking_level(thinking_level)
            await self.emit_model_select(nxt.model, previous, "cycle")

        return ModelCycleResult(
            model=nxt.model, thinking_level=self._session.thinking_level, is_scoped=True
        )

    async def _cycle_available_model(
        self, direction: str
    ) -> Optional[ModelCycleResult]:
        registry = self._session.model_registry
        available = registry.get_available()
        if len(available) <= 1:
            return None

        current = self._session.model
        current_index = next(
            (i for i, m in enumerate(available) if models_are_equal(m, current)),
            -1,
        )
        if current_index == -1:
            current_index = 0
        length = len(available)
        next_index = (
            (current_index + 1) % length
            if direction == "forward"
            else (current_index - 1 + length) % length
        )
        next_model = available[next_index]
        thinking_level = self._get_thinking_level_for_model_switch()

        previous = self._session.model
        self._session.agent.state.model = next_model
        self._session.session_manager.append_model_change(
            next_model.provider, next_model.id
        )
        settings = self._session.settings_manager
        settings.set_default_model_and_provider(next_model.provider, next_model.id)

        if previous != next_model:
            await self.set_thinking_level(thinking_level)
            await self.emit_model_select(next_model, previous, "cycle")

        return ModelCycleResult(
            model=next_model,
            thinking_level=self._session.thinking_level,
            is_scoped=False,
        )

    async def _model_has_auth(self, model: Model) -> bool:
        """检查模型是否已配置鉴权。"""
        registry = self._session.model_registry
        api_key = await registry.get_api_key(model)
        return api_key is not None and api_key != ""

    def set_scoped_models(self, scoped_models: List[object]) -> None:
        """更新 scoped models（例如来自 --models 参数）。"""
        self._session.scoped_models = scoped_models

    async def set_thinking_level(self, level: Optional[object]) -> None:
        """设置思考级别并持久化。"""
        effective = _thinking_level_from_value(level)
        previous = self._session.thinking_level

        if effective == previous:
            return

        self._session.agent.set_thinking_level(effective)
        self._session.agent.state.thinking_level = effective

        self._session.session_manager.append_thinking_level_change(effective)
        settings = self._session.settings_manager
        settings.set_default_thinking_level(effective)

        self._session._emit(ThinkingLevelChangedEvent(level=effective))
        runner = self._session._extension_runner
        if runner is not None:
            await runner.emit_thinking_level_select(
                ThinkingLevelSelectEvent(level=effective, previous_level=previous)
            )

    def _get_thinking_level_for_model_switch(
        self, explicit_level: Optional[ThinkingLevel] = None
    ) -> Optional[ThinkingLevel]:
        """切换模型时确定应使用的思考级别。"""
        if explicit_level is not None:
            return explicit_level
        if self._supports_thinking():
            return self._session.thinking_level
        settings = self._session.settings_manager
        return settings.get_default_thinking_level()

    def supports_thinking(self) -> bool:
        """当前模型是否支持 thinking/reasoning。"""
        model = self._session.model
        return bool(model is not None and getattr(model, "reasoning", False))

    # 保持与内部调用一致
    _supports_thinking = supports_thinking

    def cycle_thinking_level(self) -> Optional[ThinkingLevel]:
        """循环切换到下一个思考级别；不支持时返回 None。"""
        if not self._supports_thinking():
            return None
        levels = self.get_available_thinking_levels()
        current = self._session.thinking_level
        current_index = levels.index(current) if current in levels else -1
        next_index = (current_index + 1) % len(levels)
        next_level = levels[next_index]
        # 同步调用以兼容现有接口；实际为属性赋值+emit
        asyncio.create_task(self.set_thinking_level(next_level))
        return next_level

    def get_available_thinking_levels(self) -> List[ThinkingLevel]:
        """返回当前模型支持的思考级别列表。"""
        from nova_harness.core.utils.model_utils import get_supported_thinking_levels

        if self._session.model is None:
            return list(ThinkingLevel)
        return get_supported_thinking_levels(self._session.model)
