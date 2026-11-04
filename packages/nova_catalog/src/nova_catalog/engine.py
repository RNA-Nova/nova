"""修正语义引擎（修正知识的唯一解释点）。

知识是数据（``ProviderDefinition``），本模块是解释它的全部逻辑——
**顺序即语义**：

    ① compat 默认垫底（模型自带值赢）
    ② glob 模型级修正（语义表驱动：填缺/硬覆盖/合并）

语义表（``_FILL_ONLY``/``_HARD_OVERRIDE``/``_MERGE_DICT``）即文档——
新增修正键在此登记，不散装 if。
"""

from __future__ import annotations

import fnmatch
from typing import Any, Dict, Optional, cast

from nova_protocol import ModelFields

from .define import ProviderDefinition

# 修正键语义表（ModelFields 子集；新增键在此登记）
_HARD_OVERRIDE = {"context_window", "max_tokens"}
"""硬修正：端点实测上限等，覆盖优先。"""
_MERGE_DICT = {"compat", "cost", "thinking_level_map"}
"""按键合并：修正键覆盖源键（``thinking_level_map`` 含 ``None``——
``None`` = 该级别不支持，显式压站值），未覆盖键保留源值。站推导级别表
为底、修正键逐键压——pi ``{...effortLevels, off: null}`` 同款。"""


def _merge_dict_field(
    fields: Dict[str, Any], key: str, override: Dict[str, Any]
) -> None:
    """按键合并（修正键覆盖源键，未覆盖键保留）。"""
    fields[key] = {**(fields.get(key) or {}), **override}


def apply_corrections(
    knowledge: Optional[ProviderDefinition], model_id: str, fields: ModelFields
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
    # ① compat 默认垫底（语义特殊——默认在下、字段包自带赢，不走合并助手）
    if knowledge.compat_defaults:
        mutable["compat"] = {
            **knowledge.compat_defaults,
            **(mutable.get("compat") or {}),
        }
    # ② glob 模型级修正（语义表驱动）
    for pattern, override in knowledge.model_corrections.items():
        if not fnmatch.fnmatch(model_id, pattern):
            continue
        for key in _MERGE_DICT:
            value = override.get(key)
            if value:
                _merge_dict_field(mutable, key, value)
        for key in _HARD_OVERRIDE:
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
) -> ModelFields:
    """修正 + 盖戳（构建期与运行期 finalize 同一实现）。

    步骤（顺序即语义）：``apply_corrections`` → api 解析（源字段包自带
    优先，否则本家 ``knowledge.api``；两者皆无即响亮失败）→ 盖戳
    ``id``/``base_url``/``api``/``provider``（注解赋值，静态检查全程在线）。
    """
    corrected = apply_corrections(knowledge, model_id, ModelFields(**fields))
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


__all__ = ["apply_corrections", "stamp_fields"]
