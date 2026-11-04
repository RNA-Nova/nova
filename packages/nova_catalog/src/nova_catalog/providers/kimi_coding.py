"""Kimi Coding 的构建期知识（对位 pi ``catalog/providers/kimi-coding.ts``）。

协议：openai-completions（Chat Completions——与官方 Kimi Code 客户端同
协议，pi 同标注）；端点 ``https://api.kimi.com/coding/v1``。

族 compat 归族层（``families/kimi.py`` 的 ``K3_OPENAI_COMPAT``）；级别表
由 models.dev v2 站推导（``reasoning.py``）；本文件只收站给不了的知识：
mid-convo 旗标、always-thinking（``off: None`` 压站值）、订阅折算价
（IMPLIED_COSTS 对位——models.dev 对订阅制目录报零价）。
"""

from nova_protocol import KnownApi

from ..define import define_provider
from ..families.kimi import K3_OPENAI_COMPAT

DEF = define_provider(
    id="kimi-coding",
    name="Kimi Coding",
    base_url="https://api.kimi.com/coding/v1",
    api=KnownApi.OPENAI_COMPLETIONS,
    # models.dev 键别名：与 pi 同键（kimi-code-plan-global）；global/cn 两键
    # 清单一致，端点差异归 base_url 盖戳（本家走 api.kimi.com）
    models_dev_key="kimi-code-plan-global",
    compat_defaults={
        # 全体 Kimi Coding 模型的基线（pi KIMI_OPENAI_COMPAT 对位：deepseek
        # 方言 + max_tokens 口径）；全部模型接受对话中途的 system 文本消息
        # （pi 实测——supportsMidConvoSystemMessages 恒真）
        "thinking_format": "deepseek",
        "supports_store": False,
        "supports_developer_role": False,
        "supports_reasoning_effort": False,
        "supports_strict_mode": False,
        "max_tokens_field": "max_tokens",
        "supports_mid_convo_system_messages": True,
    },
    model_corrections={
        # Kimi Code 全部模型 always-thinking（pi 实测）——站推导的级别
        # 保留，off 一律压 None（`{...effortLevels, off: null}` 同款）
        "*": {"thinking_level_map": {"off": None}},
        # K3 系（k3 / k3-256k / k3.1-pro*）：openai 格式 + effort + 回传
        # reasoning + 接受后至的工具型 system 消息（pi KIMI_K3_OPENAI_COMPAT
        # + supportsMidConvoToolAdditions 对位）
        "k3*": {
            "compat": {
                **K3_OPENAI_COMPAT,
                "supports_mid_convo_tool_additions": True,
            },
        },
        # kimi-for-coding 同报 effort 级别，按 K3 系处理（pi.dev 实测同旗标）；
        # highspeed 变体不报 effort 且拒绝工具型 system 消息（400）——
        # 不在本修正内，走 compat_defaults。订阅折算价（IMPLIED_COSTS 对位：
        # models.dev 对订阅制报零价，按 Moonshot API 费率估算订阅用量价值）
        "kimi-for-coding": {
            "compat": {
                **K3_OPENAI_COMPAT,
                "supports_mid_convo_tool_additions": True,
            },
            "cost": {"input": 0.95, "output": 4, "cache_read": 0.19, "cache_write": 0},
        },
        "k3": {
            "cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 0},
        },
        "k3-256k": {
            # k3-256k 按 K3 费率（pi 修复：原误用低档价）
            "cost": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 0},
        },
        "k3.1-pro": {
            "cost": {"input": 2, "output": 10, "cache_read": 0.2, "cache_write": 0},
        },
        "k3.1-pro-highspeed": {
            "cost": {"input": 4, "output": 20, "cache_read": 0.2, "cache_write": 0},
        },
        "kimi-for-coding-highspeed": {
            "cost": {"input": 1.9, "output": 8, "cache_read": 0.38, "cache_write": 0},
        },
    },
)
