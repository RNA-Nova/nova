"""修正语义引擎（修正知识的唯一解释点）。

知识是数据（``ProviderDefinition``），本模块是解释它的全部逻辑——
**顺序即语义**：

    ① compat 默认垫底（模型自带值赢）
    ② glob 模型级修正（语义表驱动：填缺/硬覆盖/合并）

语义表（``_REPLACE_FIELDS``/``_MERGE_DICT``）即文档——新增修正键
在此登记，不散装 if。
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, Optional, cast

from nova_protocol import ModelFields

from .define import ProviderDefinition

# 修正键语义表（ModelFields 子集；新增键在此登记）
_REPLACE_FIELDS = {
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
"""整值替换的修正字段（与 define.py 的合法键集一致）。"""
_MERGE_DICT = {"compat", "cost", "thinking_level_map"}
"""按键合并：修正键覆盖源键（``thinking_level_map`` 含 ``None``——
``None`` = 该级别不支持，显式压站值），未覆盖键保留源值。站推导级别表
为底、修正键逐键压——pi ``{...effortLevels, off: null}`` 同款。"""


def _merge_dict_field(
    fields: Dict[str, Any], key: str, override: Dict[str, Any]
) -> None:
    """按键合并（修正键覆盖源键，未覆盖键保留）。"""
    fields[key] = {**(fields.get(key) or {}), **override}


def apply_compat_defaults(
    knowledge: Optional[ProviderDefinition], fields: ModelFields
) -> None:
    """按成品 API 应用 compat 默认；模型已声明的值始终优先。"""
    if knowledge is None:
        return
    api = fields.get("api") or knowledge.api.value
    api_value = getattr(api, "value", api)
    defaults = {
        **knowledge.compat_defaults,
        **(knowledge.compat_defaults_by_api.get(api_value) or {}),
    }
    if defaults:
        fields["compat"] = {**defaults, **(fields.get("compat") or {})}


def apply_corrections(
    knowledge: Optional[ProviderDefinition],
    model_id: str,
    fields: ModelFields,
    *,
    include_compat_defaults: bool = True,
) -> ModelFields:
    """套用 provider compat 默认 + 模型级修正（glob 匹配）。

    ``knowledge=None`` 直通（无修正的 provider——pi.dev 全供者）。
    **原地修改** ``fields`` 并返回同一对象——想保留原字段包的调用方
    须先自行拷贝（构建流水线/运行期 finalize 均已预拷贝）。

    类型纪律：公开面是 ``ModelFields``（形状声明），内部按可变 dict 工作
    （TypedDict 可变键摩擦），出口一次 ``cast`` 声明——键保留且新增键
    全在本函数控制内，cast 为诚实声明而非旁路。
    """
    if knowledge is None:
        return fields
    mutable = cast(Dict[str, Any], fields)  # 同一对象的可变视图（原地语义；
    # TypedDict→dict 为安全的 widening cast，非旁路）
    # ① compat 默认垫底；混合协议按成品 API 隔离。
    if include_compat_defaults:
        apply_compat_defaults(knowledge, fields)
    # ② glob 模型级修正（语义表驱动）
    for pattern, override in knowledge.model_corrections.items():
        if not fnmatch.fnmatch(model_id, pattern):
            continue
        for key in _MERGE_DICT:
            value = override.get(key)
            if value:
                _merge_dict_field(mutable, key, value)
        for key in _REPLACE_FIELDS:
            if key in override:
                mutable[key] = override[key]
    return cast(ModelFields, mutable)


def stamp_fields(
    knowledge: Optional[ProviderDefinition],
    model_id: str,
    fields: ModelFields,
    *,
    provider_id: str,
    base_url: Optional[str],
    include_compat_defaults: bool = True,
) -> ModelFields:
    """修正 + 盖戳；stage-one 可显式跳过 compat 默认值相位。"""
    corrected = apply_corrections(
        knowledge,
        model_id,
        ModelFields(**fields),
        include_compat_defaults=include_compat_defaults,
    )
    api = corrected.get("api") or (knowledge.api.value if knowledge else None)
    if not isinstance(api, str) or not api:
        raise ValueError(
            f"无法确定目录协议戳：{provider_id}/{model_id} "
            "源数据未携带 api 且知识未声明"
        )
    stamped = ModelFields(**corrected)
    stamped["id"] = model_id
    # base_url 为 None 时保留源数据自带值（盖戳是补全不是清空；
    # 现有调用方全传 str——None 仅理论路径）
    if base_url is not None:
        stamped["base_url"] = base_url
    stamped["api"] = api
    stamped["provider"] = provider_id
    return stamped


__all__ = ["apply_compat_defaults", "apply_corrections", "stamp_fields"]
