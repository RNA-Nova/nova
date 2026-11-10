"""模型目录契约词汇层（nova_ai 全域共享，无行为实现）：

- ``CatalogOutcome``：目录拉取结果的状态化形态（借 HTTP 响应语义表达）；
- ``CatalogService``：公开目录拉取站契约（纯匿名——无 credential）；
- ``CatalogServices``：manager 持有的站容器形状（对齐 ``ApiImpl`` 的
  单实现/dict 路由双形态）；
- ``FetchCatalogFn``：``RefreshModelsContext.catalog_fetch`` 的签名——
  manager 将 provider 选配的站经 ``partial(service.fetch, provider.id)``
  绑定后发放。

``ModelFields``/``ModelCostFields``（目录字段包形状）已迁 ``nova_protocol``
——跨"构建管线 ↔ 运行期"边界的词汇住枢纽（单一正典源），本包不再中转。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

from nova_protocol import AbortSignal, ModelFields, NovaErrorKind


class CatalogError(RuntimeError):
    """目录拉取/修正流程错误（自声明 kind——归一阶梯第 1 级消费）。

    ``nova_error_kind`` 可选：不传时归一按 ``status_code`` 的标准映射判定；
    ``status_code`` 透传 HTTP 状态给归一与遥测。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: Optional[NovaErrorKind] = None,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        if kind is not None:
            self.nova_error_kind = kind
        if status_code is not None:
            self.status_code = status_code


@dataclass(frozen=True, kw_only=True)
class CatalogOutcome:
    """目录拉取结果的状态化形态（借 HTTP 响应语义表达）。

    status：200（新目录）/ 304（未变，走缓存）/ 404 与 501（该 provider
    无远程目录）/ 0（abort 或拉取异常的合成码，无 HTTP 响应可言）/
    其他非 200（瞬时失败）。语义裁决不归本类型——归消费它的治理方。
    """

    status: int
    # frozen 只锁重绑不锁内容——纪律：构造后不再 mutate（现网调用点全部
    # 是先构建再传入；需要可变累加请新建实例）
    models_fields: List[ModelFields] = field(default_factory=list)
    # models.dev 原始 reasoningOptions 按模型 id 携带，供 finalize 的 native 门控。
    source_reasoning: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    etag: Optional[str] = None
    last_modified: Optional[int] = None
    # 拉取失败时的原始异常（status=0 的合成码场景——传输/解析错误的分类
    # 保真通道：消费方沿 __cause__ 链归一出真 kind，不再误标 UPSTREAM）。
    # 诊断链字段：不上线、不落盘（NormalizedError.cause 同款纪律）
    cause: Optional[BaseException] = None


class CatalogService(Protocol):
    """公开目录拉取站契约（纯匿名，无 credential）。

    实现只做两件事：**拉**（HTTP + 中断检查）与**翻**（站语言 →
    ``ModelFields``）——不修正、不盖戳、不治理。公开站响应人人相同；
    鉴权拉取（如订阅端点）是各家 refresh 的私有实现，不归本契约。
    """

    async def fetch(
        self,
        key: str,
        signal: Optional[AbortSignal],
        validator: Optional[str],
        skip_deprecated: bool = False,
    ) -> CatalogOutcome:
        """拉取 ``key``（provider id）对应的目录切片。

        ``validator``：上次拉取存下的 ETag（原样回显为 If-None-Match；
        不支持条件请求的站收下但忽略）。
        """
        ...


# 站容器形状（对齐 ApiImpl）：单站全员共用 / dict 按 provider.catalog_source 路由
CatalogServices = Union[CatalogService, Dict[str, CatalogService]]

# RefreshModelsContext.catalog_fetch 的签名：站经 manager 绑定 provider id
# 后的成品拉取函数——(signal, validator) -> CatalogOutcome
FetchCatalogFn = Callable[
    [Optional[AbortSignal], Optional[str]],
    Awaitable[CatalogOutcome],
]


__all__ = [
    "CatalogError",
    "CatalogOutcome",
    "CatalogService",
    "CatalogServices",
    "FetchCatalogFn",
]
