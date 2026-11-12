"""OpenAI Python SDK 的 Chat Completions 出货边界。

统一请求 payload 转成 SDK kwargs：SDK 签名声明的字段与传输旋钮直传，
其余线上字段进入 ``extra_body``。聊天流与 OpenRouter 图像调用共用此边界。
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, Mapping

from openai import Omit
from openai.resources.chat.completions import AsyncCompletions

_SDK_CREATE_KWARG_KEYS = frozenset(
    inspect.signature(AsyncCompletions.create).parameters
)
_SDK_TRANSPORT_KEYS = frozenset({"timeout", "extra_headers", "extra_query"})


def _merge_extra_body(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并开放 JSON 对象；嵌套 dict 合并，其余值由后层覆盖。"""
    merged = dict(base)
    for key, value in overlay.items():
        previous = merged.get(key)
        merged[key] = (
            _merge_extra_body(previous, value)
            if isinstance(previous, dict) and isinstance(value, dict)
            else value
        )
    return merged


def build_chat_completion_kwargs(params: Dict[str, Any]) -> Dict[str, Any]:
    """把自由请求 payload 翻成 SDK kwargs，并深合并两路 extra_body。

    ``params`` 是 provider 请求体自由负载；调用方既可把厂商扩展字段放在
    顶层，也可显式放进 ``extra_body``。输入字典不被修改。
    """
    request_params: Dict[str, Any] = {}
    inline_extra_body: Dict[str, Any] = {}
    caller_extra_body: Dict[str, Any] = dict(params.get("extra_body") or {})
    for key, value in params.items():
        if key == "extra_body":
            continue
        if key in _SDK_TRANSPORT_KEYS or key in _SDK_CREATE_KWARG_KEYS:
            request_params[key] = value
        else:
            inline_extra_body[key] = value
    extra_body = _merge_extra_body(inline_extra_body, caller_extra_body)
    if extra_body:
        request_params["extra_body"] = extra_body
    return request_params


def collect_omitted_headers(
    *layers: Mapping[str, str | None] | None,
) -> dict[str, Omit]:
    """按层序收集显式 None 头；后层非 None 可恢复同名头。

    返回请求级 ``extra_headers`` 所需的 Omit 映射，兼容 SDK 的鉴权校验。
    """
    selected: dict[str, str] = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            normalized = key.lower()
            if value is None:
                selected[normalized] = key
            else:
                selected.pop(normalized, None)
    return {key: Omit() for key in selected.values()}


__all__ = ["build_chat_completion_kwargs", "collect_omitted_headers"]
