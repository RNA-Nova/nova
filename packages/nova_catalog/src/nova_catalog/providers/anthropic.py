"""Anthropic 的构建期知识（对位 pi ``catalog/providers/anthropic.ts``）。

级别表由 models.dev v2 站推导（``reasoning.effort`` 全 Claude 系覆盖且
比手写表更全——手写表已退役）；运行时由 pi.dev 全供，故运行期修正
为零，剩余知识只有 compat 默认。
"""

from nova_protocol import KnownApi

from ..define import define_provider

DEF = define_provider(
    id="anthropic",
    name="Anthropic",
    base_url="https://api.anthropic.com",
    api=KnownApi.ANTHROPIC_MESSAGES,
    compat_defaults={
        "force_adaptive_thinking": True,
        "supports_strict_tools": True,
    },
)
