"""Model 域 JSON-RPC 方法。

模型发现（listModels）、切换（setModel）、思考等级（setThinkingLevel）、
模型轮询（cycleModel）与 scoped 模型集合（listScopedModels/setScopedModels）。
"""

from __future__ import annotations

import os
from typing import Any, List

from nova_harness.core.config.defaults import (
    AUTH_FILE_NAME,
    MODELS_FILE_NAME,
    get_agent_dir,
)
from nova_harness.server.protocol.errors import JSONRPCError
from nova_harness.server.protocol.methods import shapes
from nova_harness.server.protocol.methods.shapes import (
    CycleModelResult,
    CycleThinkingLevelResult,
    ListModelsResult,
    ListScopedModelsResult,
    ModelListItem,
    ModelRef,
    OkResult,
    ScopedModelItem,
    SetScopedModelsResult,
    SetThinkingLevelResult,
)
from nova_harness.server.protocol.methods.state import ServerState
from nova_harness.server.protocol.router import MethodRegistry


def resolve_model(model_param: Any, model_runtime: Any = None) -> Any:
    """把 RPC 参数解析为 Model（'provider/model_id' 字符串或完整 dict）。

    ``model_runtime`` 给定时走会话的运行时注册表（含扩展注册的 provider），
    否则从磁盘构造临时 ModelRuntime（createSession 尚无会话的场景）。
    """
    if isinstance(model_param, str):
        parts = model_param.split("/", 1)
        if len(parts) != 2:
            raise JSONRPCError(
                JSONRPCError.INVALID_PARAMS,
                f"Invalid model format: {model_param}. Expected 'provider/model_id'.",
            )
        return _find_model(parts[0], parts[1], model_runtime)
    elif isinstance(model_param, dict):
        from nova_ai import Model

        return Model.model_validate(model_param)
    else:
        raise JSONRPCError(
            JSONRPCError.INVALID_PARAMS,
            f"Invalid model type: {type(model_param).__name__}",
        )


def _find_model(provider: str, model_id: str, model_runtime: Any = None) -> Any:
    if model_runtime is None:
        from nova_harness.core.config import AuthStorage
        from nova_harness.core.model import ModelRuntime

        agent_dir = get_agent_dir()
        auth_path = os.path.join(agent_dir, AUTH_FILE_NAME)
        models_path = os.path.join(agent_dir, MODELS_FILE_NAME)
        auth_storage = AuthStorage.create(auth_path)
        model_runtime = ModelRuntime(auth_storage, models_path)
    model = model_runtime.find(provider, model_id)
    if model is None:
        raise JSONRPCError(
            JSONRPCError.MODEL_NOT_FOUND,
            f'Model "{provider}/{model_id}" not found in model runtime',
        )
    return model


def _serialize_model(model: Any, available_ids: set) -> ModelListItem:
    provider = getattr(model, "provider", "")
    model_id = getattr(model, "id", "")
    return ModelListItem(
        provider=provider,
        id=model_id,
        name=getattr(model, "name", "") or model_id,
        available=f"{provider}/{model_id}" in available_ids,
        reasoning=bool(getattr(model, "reasoning", False)),
    )


def register(registry: MethodRegistry, state: ServerState) -> None:
    def _session() -> Any:
        if state.runtime is None:
            raise JSONRPCError(JSONRPCError.NO_ACTIVE_SESSION, "No active session")
        return state.runtime.session

    async def listModels(params: shapes.EmptyParams) -> ListModelsResult:
        """全部已知模型（内置 + models.json + 扩展注册）及可用性标记。"""
        if state.runtime is None:
            return ListModelsResult(models=[])
        model_runtime = state.runtime.session.model_runtime
        available_ids = {
            f"{m.provider}/{m.id}" for m in model_runtime.get_available_snapshot()
        }
        return ListModelsResult(
            models=[_serialize_model(m, available_ids) for m in model_runtime.get_all()]
        )

    async def setModel(params: shapes.SetModelParams) -> OkResult:
        session = _session()
        model = resolve_model(params.model, session.model_runtime)
        ok = await session.set_model(model)
        return OkResult(success=ok)

    async def setThinkingLevel(
        params: shapes.SetThinkingLevelParams,
    ) -> SetThinkingLevelResult:
        from nova_ai import ModelThinkingLevel

        # 走会话级 API：持久化变更并广播 thinking_level_select 事件
        level = params.level
        await _session().set_thinking_level(ModelThinkingLevel(level))
        return SetThinkingLevelResult(success=True, thinking_level=level)

    async def cycleThinkingLevel(
        params: shapes.EmptyParams,
    ) -> CycleThinkingLevelResult:
        """循环切换思考级别（当前模型不支持时返回 success=False）。"""
        session = _session()
        next_level = await session.cycle_thinking_level()
        if next_level is None:
            return CycleThinkingLevelResult(
                success=False, reason="model does not support thinking"
            )
        return CycleThinkingLevelResult(success=True, thinking_level=next_level.value)

    async def cycleModel(params: shapes.CycleModelParams) -> CycleModelResult:
        """在 scoped 模型集合（或全量可用模型）中轮询切换。"""
        direction = params.direction
        result = await _session().cycle_model(direction)
        if result is None:
            return CycleModelResult(success=False)
        return CycleModelResult(
            success=True,
            model=ModelRef(provider=result.model.provider, id=result.model.id),
            thinking_level=getattr(
                result.thinking_level, "value", result.thinking_level
            ),
            is_scoped=result.is_scoped,
        )

    async def listScopedModels(params: shapes.EmptyParams) -> ListScopedModelsResult:
        """当前 scoped 模型集合（模型轮询的作用域）。"""
        scoped = _session().scoped_models
        return ListScopedModelsResult(
            models=[
                ScopedModelItem(
                    provider=entry.model.provider,
                    id=entry.model.id,
                    thinking_level=(
                        getattr(entry.thinking_level, "value", entry.thinking_level)
                        if entry.thinking_level is not None
                        else None
                    ),
                )
                for entry in scoped
            ]
        )

    async def setScopedModels(
        params: shapes.SetScopedModelsParams,
    ) -> SetScopedModelsResult:
        """设置 scoped 模型集合：``[{provider, model_id, thinking_level?}]``。"""
        from nova_harness.core.types.session.model import ScopedModelConfig

        raw = params.models
        session = _session()
        scoped: List[ScopedModelConfig] = []
        for item in raw:
            provider = item.provider
            model_id = item.model_id or item.id
            if not provider or not model_id:
                raise JSONRPCError(
                    JSONRPCError.INVALID_PARAMS,
                    "each scoped model requires 'provider' and 'model_id'",
                )
            model = session.model_runtime.find(provider, model_id)
            if model is None:
                raise JSONRPCError(
                    JSONRPCError.MODEL_NOT_FOUND,
                    f'Model "{provider}/{model_id}" not found',
                )
            scoped.append(
                ScopedModelConfig(model=model, thinking_level=item.thinking_level)
            )
        session.set_scoped_models(scoped)
        return SetScopedModelsResult(success=True, count=len(scoped))

    _D = "model"
    registry.register(
        "listModels",
        listModels,
        domain=_D,
    )
    registry.register(
        "setModel",
        setModel,
        domain=_D,
    )
    registry.register(
        "setThinkingLevel",
        setThinkingLevel,
        domain=_D,
    )
    registry.register(
        "cycleThinkingLevel",
        cycleThinkingLevel,
        domain=_D,
    )
    registry.register(
        "cycleModel",
        cycleModel,
        domain=_D,
    )
    registry.register(
        "listScopedModels",
        listScopedModels,
        domain=_D,
    )
    registry.register(
        "setScopedModels",
        setScopedModels,
        domain=_D,
    )
