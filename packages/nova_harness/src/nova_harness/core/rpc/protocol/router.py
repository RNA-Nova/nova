"""JSON-RPC 方法路由。"""

from __future__ import annotations

import inspect
import logging
import traceback
from dataclasses import dataclass
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Type,
    Union,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, ValidationError

from nova_harness.core.rpc.protocol.errors import JSONRPCError
from nova_harness.core.rpc.protocol.jsonrpc import (
    JsonRpcMessage,
    build_error,
    build_response,
)

Handler = Callable[..., Any | Awaitable[Any]]

logger = logging.getLogger(__name__)


def _resolve_shape_model(annotation: Any) -> Optional[Type[BaseModel]]:
    """从签名注解提取形状模型。

    ``BaseModel`` 子类（含 ``Optional[X]`` 解包）→ 该模型；其余注解
    （``Dict[str, Any]`` 等）→ ``None``，即自由负载语义。
    """
    if annotation is None:
        return None
    if get_origin(annotation) is Union:
        args = [a for a in annotation.__args__ if a is not type(None)]
        if len(args) == 1:
            return _resolve_shape_model(args[0])
        return None
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _derive_shape(
    method: str, handler: Handler
) -> "tuple[Optional[Type[BaseModel]], Optional[Type[BaseModel]]]":
    """从 handler 签名注解推导（params_model, result_model）。

    推导失败响亮报错——``get_type_hints`` 只查模块 globals，函数内局部
    import shapes 会让推导静默失败（形状退化为自由负载），必须在此掐断。
    """
    try:
        hints = get_type_hints(handler)
    except Exception as exc:
        raise TypeError(
            f"RPC 方法 {method} 的签名注解解析失败（检查是否函数内局部 "
            f"import shapes——get_type_hints 只查模块 globals）：{exc}"
        ) from exc
    params = list(inspect.signature(handler).parameters)
    params_model = _resolve_shape_model(hints.get(params[0])) if params else None
    result_model = _resolve_shape_model(hints.get("return"))
    return params_model, result_model


@dataclass(frozen=True)
class MethodShape:
    """一个 RPC 方法的线上形状：所属域 + 参数/结果模型。

    形状即契约：分派前的参数校验、``schema_export`` 的方法表导出、
    ``initialize`` 的能力位宣告，三方共用同一份声明。形状不再显式声明，
    注册时从 handler 签名注解自动推导（规则 9：签名即契约）。
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
    ) -> None:
        """注册一个方法处理器。

        声明 ``domain`` 的方法从签名注解自动推导形状：
        ``params: XxxParams``（``BaseModel`` 子类）→ 入参校验模型；
        ``-> XxxResult`` → 出参契约模型。自由负载方法（无固定形状）注解
        保持 ``Dict[str, Any]``，即不声明形状的语义。
        """
        self._handlers[method] = handler
        if domain is not None:
            params_model, result_model = _derive_shape(method, handler)
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
        声明了 params 模型的方法先经模型校验（类型错误/缺参 →
        ``INVALID_PARAMS``），校验通过后 handler 拿到的是模型**实例**；
        自由负载方法原样收到 dict。
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
        arg: Any = params
        if shape is not None and shape.params_model is not None:
            try:
                # camel 只存在于线上 JSON 与契约导出，不进 harness 内部代码——
                # 校验物化后 handler 体内一律属性访问
                arg = shape.params_model.model_validate(params)
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
            result = handler(arg)
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
            # 单道序列化（规则 8）：handler 返回模型实例，本层是唯一出货点
            # （dump_wire → 线上 camel）。声明了 result_model 却返回散装
            # 结果 → 契约违约，响亮报错（不告警透传）。
            if (
                shape is not None
                and shape.result_model is not None
                and result is not None
            ):
                if not isinstance(result, shape.result_model):
                    return build_error(
                        msg.id,
                        JSONRPCError(
                            JSONRPCError.INTERNAL_ERROR,
                            f"RPC 契约违约：{method} 声明返回 "
                            f"{shape.result_model.__name__} 实例，实得 "
                            f"{type(result).__name__}",
                        ),
                    )
                dump_wire = getattr(result, "dump_wire", None)
                result = (
                    dump_wire()
                    if callable(dump_wire)
                    else result.model_dump(mode="json", by_alias=True)  # RootModel 等
                )
            return build_response(msg.id, result)
        return None
