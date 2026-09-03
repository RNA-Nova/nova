"""JSON-RPC 方法路由。"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError

from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.jsonrpc import (
    JsonRpcMessage,
    build_error,
    build_notification,
    build_response,
)

Handler = Callable[[Dict[str, Any]], Any | Awaitable[Any]]

logger = logging.getLogger(__name__)


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
        """注册一个方法处理器（建议同时声明形状——校验/导出/能力位共用）。"""
        self._handlers[method] = handler
        if domain is not None:
            self._shapes[method] = MethodShape(
                domain=domain,
                params_model=params_model,
                result_model=result_model,
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

        params: Dict[str, Any] = msg.params or {}
        shape = self._shapes.get(method)
        if shape is not None and shape.params_model is not None:
            try:
                # handler 内部读 snake 键（Python 内部形态）——camel 只存在于
                # 线上 JSON 与契约导出，不进 harness 内部代码
                params = shape.params_model.model_validate(params).model_dump()
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
            # 出参归一（线上 camel）：声明了 result_model 的方法按契约校验并
            # dump_wire——此前只在入参侧校验，出参裸奔（handler 返回 snake
            # dict 直接上线——clearQueue 的 follow_up 崩了前端的 followUp）。
            # 校验失败透传原始结果并告警（契约违约要可见，但不掐断响应）。
            if (
                shape is not None
                and shape.result_model is not None
                and result is not None
            ):
                try:
                    validated = shape.result_model.model_validate(result)
                    dump_wire = getattr(validated, "dump_wire", None)
                    result = (
                        dump_wire()
                        if callable(dump_wire)
                        else validated.model_dump(
                            mode="json", by_alias=True
                        )  # RootModel 等
                    )
                except ValidationError as exc:
                    logger.warning(
                        "RPC 出参与 result_model 不符（%s）：%s", method, exc
                    )
            return build_response(msg.id, result)
        return None
