"""模型目录管线的词汇层（对位 pi ``catalog/define.ts``）。

provider 以声明式修正为主，并保留派生与两个 finalize 逃生口。构建状态不
在本层保存；hook 只接收 ``ProviderContext`` 的只读能力视图。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional

from nova_protocol import KnownApi, ModelFields


@dataclass(frozen=True)
class ProviderContext:
    """provider hook 的只读构建能力（行为容器，故用 dataclass）。"""

    provider_id: str
    strict: bool
    provider_models: Callable[[str], List[ModelFields]]
    """取另一家 stage-one 模型的深拷贝。"""
    models_dev_source: Callable[[str], Optional[Dict[str, Any]]]
    """按本 provider 的模型 id 取原始 models.dev 条目；无来源返回 None。"""


@dataclass(frozen=True)
class ProviderDefinition:
    """一家 provider 的构建期知识全集。"""

    id: str
    name: str
    base_url: str
    api: KnownApi
    models_dev_key: Optional[str] = None
    skip_deprecated: bool = False
    compat_defaults: Mapping[str, Any] = field(default_factory=dict)
    compat_defaults_by_api: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    """混合协议 provider 的 per-API compat 默认；覆盖通用默认的同名键。"""
    model_corrections: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    pinned_models: Mapping[str, ModelFields] = field(default_factory=dict)
    map_model: Optional[Callable[[str, ModelFields, Dict[str, Any]], None]] = None
    """源条目映射缝：stage-one 内运行，参数为 id、fields、原始 models.dev 条目。"""
    derive_from: Optional[str] = None
    finalize: Optional[Callable[[str, ModelFields, ProviderContext], None]] = None
    """per-model 逃生口：共享规则之后、图像默认之前原地修改 fields。"""
    finalize_all: Optional[Callable[[List[ModelFields], ProviderContext], None]] = None
    """跨模型逃生口：整表定稿前原地修改列表。"""


_CORRECTION_KEYS = frozenset(
    {
        "compat",
        "cost",
        "thinking_level_map",
        "context_window",
        "max_tokens",
        "reasoning",
        "headers",
        "input_types",
        "input_limits",
        "prompt_cache",
        "sampling_params",
        "sampling_params_by_thinking_level",
    }
)


def define_provider(
    *,
    id: str,
    name: str,
    base_url: str,
    api: KnownApi,
    models_dev_key: Optional[str] = None,
    skip_deprecated: bool = False,
    compat_defaults: Optional[Mapping[str, Any]] = None,
    compat_defaults_by_api: Optional[Mapping[str, Mapping[str, Any]]] = None,
    model_corrections: Optional[Mapping[str, Mapping[str, Any]]] = None,
    pinned_models: Optional[Mapping[str, ModelFields]] = None,
    map_model: Optional[Callable[[str, ModelFields, Dict[str, Any]], None]] = None,
    derive_from: Optional[str] = None,
    finalize: Optional[Callable[[str, ModelFields, ProviderContext], None]] = None,
    finalize_all: Optional[Callable[[List[ModelFields], ProviderContext], None]] = None,
) -> ProviderDefinition:
    """构造 provider 定义；修正包出现未知键即构建期响亮失败。"""
    for pattern, override in (model_corrections or {}).items():
        if not isinstance(override, Mapping):
            raise TypeError(f"Correction for {pattern!r} must be a mapping")
        unknown = set(override) - _CORRECTION_KEYS
        if unknown:
            raise ValueError(
                f"Unknown correction keys for {pattern!r}: {sorted(unknown)}"
            )
    for api_key in compat_defaults_by_api or {}:
        try:
            KnownApi(api_key)
        except ValueError:
            raise ValueError(
                f"Unknown compat_defaults_by_api key: {api_key!r}"
            ) from None
    return ProviderDefinition(
        id=id,
        name=name,
        base_url=base_url,
        api=api,
        models_dev_key=models_dev_key,
        skip_deprecated=skip_deprecated,
        compat_defaults=compat_defaults or {},
        compat_defaults_by_api=compat_defaults_by_api or {},
        model_corrections=model_corrections or {},
        pinned_models=pinned_models or {},
        map_model=map_model,
        derive_from=derive_from,
        finalize=finalize,
        finalize_all=finalize_all,
    )


__all__ = ["ProviderContext", "ProviderDefinition", "define_provider"]
