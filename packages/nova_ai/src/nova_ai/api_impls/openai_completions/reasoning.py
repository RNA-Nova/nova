"""OpenAI reasoning details 结构化体系（对齐 TS 2026-08 reasoning details 终态）。

三种 detail 形态：``reasoning.summary`` / ``reasoning.encrypted`` /
``reasoning.text``。流式期间 OpenRouter 把 details 以 delta 形式推送——
同类型相邻 delta 拼接为逻辑条目，encrypted 保持离散不透明。归档位置是
thinking 块的 ``thinking_signature``（JSON 数组），跨模型/跨 API 重放时
随块保留；``toolCall.thought_signature`` 仅作旧会话兜底读取。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, NotRequired, Optional, TypedDict, Union

# 顶层 reasoning 字段白名单（对齐 TS OPENAI_COMPLETIONS_REASONING_FIELDS）：
# thinking_signature 会作为请求体字段名发送，绝不允许白名单之外的值。
OPENAI_COMPLETIONS_REASONING_FIELDS = (
    "reasoning",
    "reasoning_content",
    "reasoning_text",
)


def is_reasoning_field(field: str) -> bool:
    """是否为合法的顶层 reasoning 字段名（对齐 TS isOpenAICompletionsReasoningField）。"""
    return field in OPENAI_COMPLETIONS_REASONING_FIELDS


class _ReasoningDetailCommon(TypedDict, total=False):
    """三形态共享的可选元字段。"""

    id: str
    format: str
    index: int


class ReasoningSummaryDetail(_ReasoningDetailCommon):
    """``type == "reasoning.summary"``。"""

    type: Literal["reasoning.summary"]
    summary: str


class ReasoningEncryptedDetail(_ReasoningDetailCommon):
    """``type == "reasoning.encrypted"``——离散不透明条目。"""

    type: Literal["reasoning.encrypted"]
    data: str


class ReasoningTextDetail(_ReasoningDetailCommon):
    """``type == "reasoning.text"``。"""

    type: Literal["reasoning.text"]
    text: str
    signature: NotRequired[str]


ReasoningDetail = Union[ReasoningSummaryDetail, ReasoningEncryptedDetail, ReasoningTextDetail]
"""判别键 ``type``（规则 6：解析边界手写判别已有——``is_openai_reasoning_detail``；
形状在此声明供归档/重放路径引用）。"""


def _is_reasoning_detail_object(detail: Any) -> bool:
    return isinstance(detail, dict)


def _has_valid_common_fields(candidate: Dict[str, Any]) -> bool:
    return (
        (candidate.get("id") is None or isinstance(candidate.get("id"), str))
        and (
            candidate.get("format") is None or isinstance(candidate.get("format"), str)
        )
        and (candidate.get("index") is None or isinstance(candidate.get("index"), int))
    )


def is_openai_reasoning_detail(detail: Any) -> bool:
    """是否为合法的 reasoning detail（三种类型之一；对齐 TS isOpenAIReasoningDetail）。"""
    if not _is_reasoning_detail_object(detail) or not _has_valid_common_fields(detail):
        return False
    detail_type = detail.get("type")
    if detail_type == "reasoning.summary":
        return isinstance(detail.get("summary"), str)
    if detail_type == "reasoning.encrypted":
        return isinstance(detail.get("data"), str)
    if detail_type == "reasoning.text":
        return isinstance(detail.get("text"), str) and (
            detail.get("signature") is None or isinstance(detail.get("signature"), str)
        )
    return False


def parse_openai_reasoning_details(
    signature: Optional[str],
) -> Optional[List[ReasoningDetail]]:
    """把 thinking 块签名解析为 detail 数组；非法/空返回 None（对齐 TS parseOpenAIReasoningDetails）。"""
    if not signature:
        return None
    try:
        parsed = json.loads(signature)
    except Exception:
        return None
    if (
        isinstance(parsed, list)
        and len(parsed) > 0
        and all(is_openai_reasoning_detail(item) for item in parsed)
    ):
        return parsed
    return None


def parse_legacy_encrypted_reasoning_detail(
    signature: Optional[str],
) -> Optional[Dict[str, Any]]:
    """解析旧会话 toolCall.thought_signature 里的单个加密 detail（对齐 TS parseLegacyEncryptedReasoningDetail）。"""
    if not signature:
        return None
    try:
        parsed = json.loads(signature)
    except Exception:
        return None
    if (
        is_openai_reasoning_detail(parsed)
        and parsed.get("type") == "reasoning.encrypted"
        and isinstance(parsed.get("id"), str)
        and len(parsed["id"]) > 0
        and len(parsed["data"]) > 0
    ):
        return parsed
    return None


def _fill_missing_common_fields(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    if target.get("id") is None:
        target["id"] = source.get("id")
    if not target.get("format"):
        target["format"] = source.get("format")
    if target.get("index") is None:
        target["index"] = source.get("index")


def append_openai_reasoning_detail(
    details: List[Dict[str, Any]], detail: Dict[str, Any]
) -> None:
    # 注：delta 合并对开放 dict 做动态手术（相邻同类型拼接、公共字段回填），
    # 保持 Dict[str, Any]；TypedDict 形状服务 parse/archive 边界（见上）。
    """拼接 reasoning detail delta（对齐 TS appendOpenAIReasoningDetail）。

    OpenRouter 以 delta 推送 details：相邻同类型的 text/summary 合并为
    逻辑条目；encrypted 保持离散不透明。签名取首个非空值。
    """
    last = details[-1] if details else None
    if (
        detail.get("type") == "reasoning.text"
        and last is not None
        and last.get("type") == "reasoning.text"
    ):
        last["text"] += detail["text"]
        if not last.get("signature"):
            last["signature"] = detail.get("signature")
        _fill_missing_common_fields(last, detail)
        return
    if (
        detail.get("type") == "reasoning.summary"
        and last is not None
        and last.get("type") == "reasoning.summary"
    ):
        last["summary"] += detail["summary"]
        _fill_missing_common_fields(last, detail)
        return
    details.append(dict(detail))


__all__ = [
    "OPENAI_COMPLETIONS_REASONING_FIELDS",
    "append_openai_reasoning_detail",
    "is_openai_reasoning_detail",
    "is_reasoning_field",
    "parse_legacy_encrypted_reasoning_detail",
    "parse_openai_reasoning_details",
]
