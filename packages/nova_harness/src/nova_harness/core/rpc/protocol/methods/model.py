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
from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.methods import shapes as _sh
from nova_harness.core.rpc.protocol.methods.state import ServerState
from nova_harness.core.rpc.protocol.router import MethodRegistry

_D = "model"


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


def _model_list_item(model: Any, available_ids: set) -> _sh.ModelListItem:
    provider = getattr(model, "provider", "")
    model_id = getattr(model, "id", "")
    return _sh.ModelListItem(
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

    async def listModels(params: _sh.EmptyParams) -> _sh.ListModelsResult:
        """全部已知模型（内置 + models.json + 扩展注册）及可用性标记。"""
        if state.runtime is None:
            return _sh.ListModelsResult(models=[])
        model_runtime = state.runtime.session.model_runtime
        available_ids = {
            f"{m.provider}/{m.id}" for m in model_runtime.get_available_snapshot()
        }
        return _sh.ListModelsResult(
            models=[
                _model_list_item(m, available_ids) for m in model_runtime.get_all()
            ],
        )

    async def setModel(params: _sh.SetModelParams) -> _sh.OkResult:
        session = _session()
        model = resolve_model(params.model, session.model_runtime)
        ok = await session.set_model(model)
        return _sh.OkResult(ok=ok)

    async def setThinkingLevel(
        params: _sh.SetThinkingLevelParams,
    ) -> _sh.SetThinkingLevelResult:
        from nova_ai import ModelThinkingLevel

        # 走会话级 API：持久化变更并广播 thinking_level_select 事件
        await _session().set_thinking_level(ModelThinkingLevel(params.level))
        return _sh.SetThinkingLevelResult(ok=True, thinking_level=params.level)

    async def cycleThinkingLevel(
        params: _sh.EmptyParams,
    ) -> _sh.CycleThinkingLevelResult:
        """循环切换思考级别（当前模型不支持时返回 ok=False）。"""
        session = _session()
        next_level = await session.cycle_thinking_level()
        if next_level is None:
            return _sh.CycleThinkingLevelResult(
                ok=False, reason="model does not support thinking"
            )
        return _sh.CycleThinkingLevelResult(ok=True, thinking_level=next_level.value)

    async def cycleModel(params: _sh.CycleModelParams) -> _sh.CycleModelResult:
        """在 scoped 模型集合（或全量可用模型）中轮询切换。"""
        result = await _session().cycle_model(params.direction)
        if result is None:
            return _sh.CycleModelResult(ok=False)
        return _sh.CycleModelResult(
            ok=True,
            model=_sh.ModelRef(provider=result.model.provider, id=result.model.id),
            thinking_level=getattr(
                result.thinking_level, "value", result.thinking_level
            ),
            is_scoped=result.is_scoped,
        )

    async def listScopedModels(params: _sh.EmptyParams) -> _sh.ListScopedModelsResult:
        """当前 scoped 模型集合（模型轮询的作用域）。"""
        scoped = _session().scoped_models
        return _sh.ListScopedModelsResult(
            models=[
                _sh.ScopedModelItem(
                    provider=entry.model.provider,
                    id=entry.model.id,
                    thinking_level=(
                        getattr(entry.thinking_level, "value", entry.thinking_level)
                        if entry.thinking_level is not None
                        else None
                    ),
                )
                for entry in scoped
            ],
        )

    async def setScopedModels(
        params: _sh.SetScopedModelsParams,
    ) -> _sh.SetScopedModelsResult:
        """设置 scoped 模型集合：``[{provider, model_id, thinking_level?}]``。"""
        from nova_harness.core.types.session.model import ScopedModelConfig

        session = _session()
        scoped: List[ScopedModelConfig] = []
        for item in params.models:
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
        return _sh.SetScopedModelsResult(ok=True, count=len(scoped))

    registry.register("listModels", listModels, domain=_D)
    registry.register("setModel", setModel, domain=_D)
    registry.register("setThinkingLevel", setThinkingLevel, domain=_D)
    registry.register("cycleThinkingLevel", cycleThinkingLevel, domain=_D)
    registry.register("cycleModel", cycleModel, domain=_D)
    registry.register("listScopedModels", listScopedModels, domain=_D)
    registry.register("setScopedModels", setScopedModels, domain=_D)
