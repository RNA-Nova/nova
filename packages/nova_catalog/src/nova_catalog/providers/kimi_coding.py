"""Kimi Coding 的构建期知识（对位 pi ``catalog/providers/kimi-coding.ts``）。

级别表由 models.dev v2 站推导——手写表已退役；本文件只收站给不了
的知识：adaptive 旗标、allow_empty_signature、**always-thinking**
（pi 实测：Kimi Code 全部模型 always-thinking，``off: null`` 压站值）、
订阅折算价（IMPLIED_COSTS 对位）。

注意：K3 的 openai 协议面 compat（thinking_format=openai 等）**不在本
文件**——本家当前走 anthropic 协议，该面随协议切换（openai-completions）
一并落地。
"""

from nova_protocol import KnownApi

from ..define import define_provider

DEF = define_provider(
    id="kimi-coding",
    name="Kimi Coding",
    base_url="https://api.kimi.com/coding",
    api=KnownApi.ANTHROPIC_MESSAGES,
    # models.dev 键别名：本家在站上的键是 kimi-code-plan-cn
    models_dev_key="kimi-code-plan-cn",
    compat_defaults={
        # 全部 kimi coding 模型：adaptive thinking（pi.dev 全目录实测）
        "force_adaptive_thinking": True,
    },
    model_corrections={
        # Kimi Code 全部模型 always-thinking（pi 实测）——站推导的级别
        # 保留，off 一律压 None（`{...effortLevels, off: null}` 同款）
        "*": {"thinking_level_map": {"off": None}},
        "k3": {
            # allowEmptySignature 仅 k3 与 kimi-for-coding（pi.dev 实测——
            # k3-256k / highspeed 没有）
            "compat": {"allow_empty_signature": True},
            # 订阅折算价：k3 按 Kimi K3 API 费率（pi IMPLIED_COSTS 同款）
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
        "kimi-for-coding": {
            "compat": {"allow_empty_signature": True},
            "cost": {"input": 0.95, "output": 4, "cache_read": 0.19, "cache_write": 0},
        },
        # highspeed 不报 effort 且拒绝工具型 system 消息（pi.dev 实测）——
        # 级别表仅 "*" 的 off:None，无补充；仅折算价
        "kimi-for-coding-highspeed": {
            "cost": {"input": 1.9, "output": 8, "cache_read": 0.38, "cache_write": 0},
        },
    },
)
