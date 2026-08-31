"""JSON-RPC 方法路由。"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type, get_type_hints

from pydantic import BaseModel, RootModel, ValidationError

from nova_ai.types.base_model import NovaBaseModel

from nova_harness.server.protocol.errors import JSONRPCError
from nova_harness.server.protocol.jsonrpc import (
    JsonRpcMessage,
    build_error,
    build_notification,
    build_response,
)

Handler = Callable[[Dict[str, Any]], Any | Awaitable[Any]]

logger = logging.getLogger(__name__)


def _as_model(annotation: Any) -> Optional[Type[BaseModel]]:
    """注解若是 Pydantic 模型类则返回之（含 RootModel；否则 None）。"""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _infer_handler_shapes(
    handler: Any,
) -> tuple[Optional[Type[BaseModel]], Optional[Type[BaseModel]]]:
    """从 handler 签名注解推导形状（签名即声明——codex 宏定义的运行时对位）。

    返回 (params_model, result_model)。注解缺失/无法解析时为 None
    （自由负载方法——不声明形状，入参不校验、出参直出）。
    """
    try:
        hints = get_type_hints(handler)
    except Exception:
        return None, None
    return _as_model(hints.get("params")), _as_model(hints.get("return"))


@dataclass(frozen=True)
class MethodShape:
    """一个 RPC 方法的线上形状：所属域 + 参数/结果模型。

    形状即契约：分派前的参数校验、``schema_export`` 的方法表导出、
    ``initialize`` 的能力位宣告，三方共用同一份声明。
    """

    domain: str
    params_model: Optional[Type[BaseModel]] = None
    result_model: Optional[Type[BaseModel]] = None


class MethodRegistry:
    """JSON-RPC 方法注册表（处理器 + 线上形状）。"""

    def __init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}
        self._shapes: Dict[str, MethodShape] = {}

    def register(
        self,
        method: str,
        handler: Handler,
        *,
        domain: Optional[str] = None,
        params_model: Optional[Type[BaseModel]] = None,
        result_model: Optional[Type[BaseModel]] = None,
    ) -> None:
        """注册一个方法处理器（建议同时声明形状——校验/导出/能力位共用）。

        形状优先从 handler 签名注解推导（``params: XxxParams ->
        XxxResult``——签名即声明，注册处零重复）；显式参数仅作推导失败
        时的兜底/覆盖（自由负载方法注解为 Dict 时经显式声明补形状）。
        """
        inferred_params, inferred_result = _infer_handler_shapes(handler)
        self._handlers[method] = handler
        if domain is not None:
            self._shapes[method] = MethodShape(
                domain=domain,
                params_model=params_model or inferred_params,
                result_model=result_model or inferred_result,
            )

    def register_many(self, methods: Dict[str, Handler]) -> None:
        """批量注册方法处理器。"""
        self._handlers.update(methods)

    def unregister(self, method: str) -> None:
        """注销方法处理器（连同形状一并移除）。"""
        self._handlers.pop(method, None)
        self._shapes.pop(method, None)

    def has(self, method: str) -> bool:
        return method in self._handlers

    def method_names(self) -> List[str]:
        """全部已注册方法名（能力位宣告的数据源）。"""
        return sorted(self._handlers)

    def shapes(self) -> Dict[str, MethodShape]:
        """全部已声明的方法形状（schema 导出的数据源）。"""
        return dict(self._shapes)

    def domains(self) -> Dict[str, List[str]]:
        """域 → 方法名列表（能力位宣告的数据源）。"""
        result: Dict[str, List[str]] = {}
        for name, shape in self._shapes.items():
            result.setdefault(shape.domain, []).append(name)
        for names in result.values():
            names.sort()
        return dict(sorted(result.items()))

    async def dispatch(self, msg: JsonRpcMessage) -> Optional[JsonRpcMessage]:
        """分发一条 JSON-RPC 请求/通知。

        通知没有 id，不返回响应；请求返回响应或错误。
        声明了 ``params_model`` 的方法先经模型校验（类型错误/缺参 →
        ``INVALID_PARAMS``），校验通过后以规范化 dict 调用处理器。
        """
        method = msg.method
        if method is None:
            return None
        handler = self._handlers.get(method)
        if handler is None:
            if msg.id is not None:
                return build_error(
                    msg.id,
                    JSONRPCError(
                        JSONRPCError.METHOD_NOT_FOUND, f"Method not found: {method}"
                    ),
                )
            return None

        params: Dict[str, Any] | BaseModel = msg.params or {}
        shape = self._shapes.get(method)
        if shape is not None and shape.params_model is not None:
            try:
                # 入站校验（camel→模型字段归一）后传**实例**——handler 签名
                # 即契约，体内一律属性访问（散装取键已消亡）
                params = shape.params_model.model_validate(params)
            except ValidationError as exc:
                if msg.id is not None:
                    return build_error(
                        msg.id,
                        JSONRPCError(
                            JSONRPCError.INVALID_PARAMS,
                            f"Invalid params for {method}: {exc}",
                        ),
                    )
                return None

        try:
            result = handler(params)
            if isinstance(result, Awaitable):
                result = await result
        except JSONRPCError as exc:
            if msg.id is not None:
                return build_error(msg.id, exc)
            return None
        except Exception as exc:
            traceback.print_exc()
            if msg.id is not None:
                return build_error(
                    msg.id,
                    JSONRPCError(JSONRPCError.INTERNAL_ERROR, str(exc)),
                )
            return None

        if msg.id is not None:
            if result is not None:
                # 实例直通（单道出货）：handler 返回模型实例——构造即契约，
                # dump_wire 一次出货，全程无重建。
                if isinstance(result, NovaBaseModel):
                    if (
                        shape is not None
                        and shape.result_model is not None
                        and not isinstance(result, shape.result_model)
                    ):
                        # 契约违约响亮失败（codex 编译期强制的运行时对位）——
                        # 宁可让调用方拿到明确错误，不静默带病上线
                        return build_error(
                            msg.id,
                            JSONRPCError(
                                JSONRPCError.INTERNAL_ERROR,
                                f"RPC 出参实例类型与声明不符（{method}）："
                                f"{type(result).__name__}，应为 "
                                f"{shape.result_model.__name__}",
                            ),
                        )
                    result = result.dump_wire()
                elif isinstance(result, RootModel):
                    result = result.model_dump(mode="json", by_alias=True)
                elif shape is not None and shape.result_model is not None:
                    # 声明了 result_model 但返回散装 dict——契约违约
                    #（自由负载方法不声明 result_model，dict 直出不进此分支）
                    return build_error(
                        msg.id,
                        JSONRPCError(
                            JSONRPCError.INTERNAL_ERROR,
                            f"RPC 出参必须为 {shape.result_model.__name__} "
                            f"实例（{method}），收到 {type(result).__name__}",
                        ),
                    )
            return build_response(msg.id, result)
        return None
