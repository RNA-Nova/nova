"""Anthropic 的构建期知识（对位 pi ``catalog/providers/anthropic.ts``
与 ``catalog/families/anthropic.ts``）。

家族规则（``_finalize``，pi ``applyClaudeFamilyRules`` 对位）：级别表
兜底链（max 全系 → xhigh 新代 → fable-5 三件套 → 5.5 手写表）、
``force_adaptive_thinking`` 名单制（pi ``ADAPTIVE_THINKING``——非全
Claude 一刀切）、``supports_temperature=False`` 名单（5 代）、1M 上
下文两系、``prompt_cache`` 与 ``input_limits`` 全供。级别表主源是
models.dev v2 站推导（``reasoning.effort``），手写表仅兜底。mid-convo
旗标组走 ``model_corrections`` 声明（对位 pi 的两个 supports 函数）。
"""

import fnmatch
from typing import Any, Dict, List, cast

from nova_protocol import KnownApi, ModelFields, ThinkingLevelMap

from ..define import define_provider

# 对话中段 system 消息 + 工具增删（pi supportsClaudeMidConvoSystemMessages）：
# opus-4.8/5/5.5、sonnet-5.5、haiku-5.5、fable/mythos-5/5.1
_MID_CONVO_SYSTEM = {
    "supports_mid_convo_system_messages": True,
    "supports_mid_convo_tool_changes": True,
}

# 对话中途变更思考级别（pi supportsClaudeMidConvoEffort）：
# opus-5/5.5、sonnet-5.5、haiku-5.5、fable/mythos-5.1（注意不含 opus-4.8 与
# fable/mythos-5.0）
_MID_CONVO_EFFORT = {"supports_mid_convo_effort": True}

_MID_CONVO_EFFORT_GLOBS = (
    "claude-opus-5*",
    "claude-sonnet-5-5*",
    "claude-haiku-5-5*",
    "claude-fable-5-1*",
    "claude-mythos-5-1*",
)

# adaptive thinking 名单（pi ``ADAPTIVE_THINKING``——子串匹配，非全 Claude）
_ADAPTIVE_THINKING_FRAGMENTS = (
    "opus-4-6",
    "opus-4.6",
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-4-6",
    "sonnet-4.6",
    "sonnet-5",
    "sonnet.5",
    "haiku-5",
    "haiku.5",
    "fable-5",
    "mythos-5",
)

# temperature 不支持名单（pi ``TEMPERATURE_UNSUPPORTED``——5 代起）
_TEMPERATURE_UNSUPPORTED_FRAGMENTS = (
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-5-5",
    "sonnet-5.5",
    "haiku-5-5",
    "haiku-5.5",
)

# 级别表兜底链（pi ``applyClaudeFamilyRules`` 的三条 TLM 规则）
_MAX_EFFORT_ONLY_FRAGMENTS = ("opus-4-6", "opus-4.6", "sonnet-4-6", "sonnet-4.6")
_XHIGH_AND_MAX_EFFORT_FRAGMENTS = (
    "opus-4-7",
    "opus-4.7",
    "opus-4-8",
    "opus-4.8",
    "opus-5",
    "opus.5",
    "sonnet-5",
    "sonnet.5",
    "haiku-5",
    "haiku.5",
)

# 5.5 系（pi ``MODELS_55``——站 effort 元数据可能不全，手写表兜底）
_MODELS_55 = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")

# 1M 上下文两系（pi ``MODELS_WITH_1M_CONTEXT``）
_MODELS_WITH_1M_CONTEXT = ("claude-opus-4-6", "claude-sonnet-4-6")

# 不能接受 thinking disabled 的模型（pi ``DISABLED_THINKING_MODELS``——
# enableClaudeMidConvoEffort 给其余 effort 模型补 off:null）
_DISABLED_THINKING_MODELS = {"claude-opus-5", "claude-haiku-5-5", "claude-haiku-5.5"}

# 服务端 refusal fallback 链（pi ``ALLOWED_FALLBACK_MODELS`` 对位）
_ALLOWED_FALLBACK_MODELS = {
    "claude-fable-5": ["claude-opus-4-8", "claude-opus-5"],
    "claude-opus-5": ["claude-opus-4-8"],
}


def _supports_mid_convo_effort(model_id: str) -> bool:
    """mid-convo effort 支持面（pi ``supportsClaudeMidConvoEffort`` 对位）。"""
    return any(fnmatch.fnmatch(model_id, glob) for glob in _MID_CONVO_EFFORT_GLOBS)


def _includes_any(model_id: str, fragments) -> bool:
    return any(fragment in model_id for fragment in fragments)


def _claude55_thinking_levels(model_id: str) -> Dict[str, Any]:
    """5.5 手写级别表（pi ``claude55ThinkingLevels``——haiku 可关，其余 off:null）。"""
    return {
        **({} if "haiku" in model_id else {"off": None}),
        "minimal": None,
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "max",
    }


def _finalize(model_id: str, fields: "ModelFields", ctx: Any) -> None:
    """per-model 逃生口（pi ``applyClaudeFamilyRules`` + anthropic finalize 对位）。"""
    # 1M 上下文（pi 直接改写 contextWindow）
    if model_id in _MODELS_WITH_1M_CONTEXT:
        fields["context_window"] = 1000000

    # 级别表兜底链（站推导已填的不覆盖——merge 语义：只补缺键）
    level_map: Dict[str, Any] = dict(fields.get("thinking_level_map") or {})
    if _includes_any(model_id, _MAX_EFFORT_ONLY_FRAGMENTS):
        level_map.setdefault("max", "max")
    if _includes_any(model_id, _XHIGH_AND_MAX_EFFORT_FRAGMENTS):
        level_map.setdefault("xhigh", "xhigh")
        level_map.setdefault("max", "max")
    if "fable-5" in model_id:
        level_map["off"] = None
        level_map["xhigh"] = "xhigh"
        level_map["max"] = "max"
    if model_id in _MODELS_55:
        for key, value in _claude55_thinking_levels(model_id).items():
            level_map.setdefault(key, value)
    # mid-convo effort 且可关思考的：off 显式 null（pi enableClaudeMidConvoEffort）
    compat = dict(fields.get("compat") or {})
    if (
        compat.get("supports_mid_convo_effort")
        and model_id not in _DISABLED_THINKING_MODELS
    ):
        level_map.setdefault("off", None)
    if level_map:
        # ThinkingLevelMap 即 Dict[str, Optional[str]]（cast 收口——声明形状）
        fields["thinking_level_map"] = cast("ThinkingLevelMap", level_map)

    # adaptive thinking 名单制（pi——非全 Claude 一刀切）
    if _includes_any(model_id, _ADAPTIVE_THINKING_FRAGMENTS):
        compat["force_adaptive_thinking"] = True
    if _includes_any(model_id.lower(), _TEMPERATURE_UNSUPPORTED_FRAGMENTS):
        compat["supports_temperature"] = False
    fields["compat"] = compat

    # prompt cache 寿命（pi——仅 anthropic 直销标注，短 300s / 长 3600s）
    fields["prompt_cache"] = {"short": 300, "long": 3600}
    # 输入限制（pi withInputLimits——32MB 请求体；按窗口分档的每请求图片数）
    context_window = fields.get("context_window") or 0
    fields["input_limits"] = {
        "max_request_bytes": 32 * 1024 * 1024,
        "images": {"max_per_request": 100 if context_window == 200000 else 600},
    }


def _wire_fallback_models(models: List["ModelFields"], ctx) -> None:
    """fallback 链接线（跨模型逃生口，pi ``finalizeAll`` 对位）：

    源模型支持 mid-convo effort 时，候选目标收窄到同支持该特性的
    （转录里的 effort 标记需要目标端点也认）；目标须在目录内（带其
    成本——fallback 应答按目标费率计）。"""
    by_id = {m.get("id"): m for m in models}
    for model_id, fallback_ids in _ALLOWED_FALLBACK_MODELS.items():
        model = by_id.get(model_id)
        if model is None:
            continue
        source_compat = model.get("compat") or {}
        candidates = (
            [fid for fid in fallback_ids if _supports_mid_convo_effort(fid)]
            if source_compat.get("supports_mid_convo_effort")
            else fallback_ids
        )
        entries = [
            {
                "provider": "anthropic",
                "model": fid,
                "cost": dict(by_id[fid].get("cost") or {}),
            }
            for fid in candidates
            if fid in by_id
        ]
        if entries:
            model["compat"] = {
                **(model.get("compat") or {}),
                "allowed_fallback_models": entries,
            }


DEF = define_provider(
    id="anthropic",
    name="Anthropic",
    base_url="https://api.anthropic.com",
    api=KnownApi.ANTHROPIC_MESSAGES,
    compat_defaults={
        "supports_strict_tools": True,
    },
    finalize=_finalize,
    finalize_all=_wire_fallback_models,
    model_corrections={
        "claude-opus-4-8*": {"compat": dict(_MID_CONVO_SYSTEM)},
        "claude-opus-5*": {"compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT}},
        "claude-sonnet-5-5*": {"compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT}},
        "claude-haiku-5-5*": {"compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT}},
        "claude-fable-5*": {"compat": dict(_MID_CONVO_SYSTEM)},
        "claude-fable-5-1*": {
            "compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT},
        },
        "claude-mythos-5*": {"compat": dict(_MID_CONVO_SYSTEM)},
        "claude-mythos-5-1*": {
            "compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT},
        },
    },
)
