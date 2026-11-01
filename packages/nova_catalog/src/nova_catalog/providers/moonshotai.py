"""Moonshot AI 的构建期知识（对位 pi ``catalog/providers/moonshotai.ts``）。

族 compat 归族层（``families/kimi.py``）；级别表由 models.dev v2 的
``reasoning.effort`` **站推导**（``reasoning.py``）——手写表已退役；
deferred tools 旗标与计费口径修正是站给不了的知识，归本文件。
moonshotai_cn 与本家同构（仅 id/base_url 不同）——共享本文件的定义件。
"""

from nova_protocol import KnownApi

from ..define import define_provider
from ..families.kimi import K3_OPENAI_COMPAT

# k2.7-code 系：always-thinking（官方文档：thinking disabled 被拒——
# pi ALWAYS_THINKING_MODEL_IDS 对位；站推导其余级别，off 由本键压死）
_TLM_OFF_ONLY = {"off": None}

# moonshotai / moonshotai-cn 共享的 compat 默认（deepseek 方言——k2.x 家族）
MOONSHOTAI_COMPAT_DEFAULTS = {
    "thinking_format": "deepseek",
    "supports_store": False,
    "supports_developer_role": False,
    "supports_reasoning_effort": False,
    "supports_strict_mode": False,
    "max_tokens_field": "max_tokens",
}

# moonshotai / moonshotai-cn 共享的模型级修正
MOONSHOTAI_MODEL_CORRECTIONS = {
    "kimi-k2.7-code": {"thinking_level_map": dict(_TLM_OFF_ONLY)},
    "kimi-k2.7-code-highspeed": {"thinking_level_map": dict(_TLM_OFF_ONLY)},
    # K3 族（glob 覆盖 kimi-k3 / kimi-k3.1-pro / kimi-k3.1-pro-highspeed 及
    # 后续点发布）：pi.dev 实测——K3 族走 openai 格式 + effort（不是
    # deepseek，k2.x 才是）；中途 system 消息与工具追加是 K3 的端点能力
    # （pi 的 supportsMidConvo* 对位）；级别表归站推导，不再声明
    "kimi-k3*": {
        "compat": {
            **K3_OPENAI_COMPAT,
            "supports_mid_convo_system_messages": True,
            "supports_mid_convo_tool_additions": True,
        },
        # Moonshot 不收缓存写费；models.dev 把 input 价错标为 cache_write
        # （pi 同款修正——K3 族 cache_write 一律归零，保留源 input/output）
        "cost": {"cache_write": 0},
    },
    # kimi-k3 全价修正（pi 同款：models.dev 该条目价格字段口径错误，
    # 以 K3 官方价目为准）
    "kimi-k3": {
        "cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 0},
    },
}

DEF = define_provider(
    id="moonshotai",
    name="Moonshot AI",
    base_url="https://api.moonshot.ai/v1",
    api=KnownApi.OPENAI_COMPLETIONS,
    compat_defaults=MOONSHOTAI_COMPAT_DEFAULTS,
    model_corrections=MOONSHOTAI_MODEL_CORRECTIONS,
)
