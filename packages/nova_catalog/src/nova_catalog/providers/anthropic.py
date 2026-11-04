"""Anthropic 的构建期知识（对位 pi ``catalog/providers/anthropic.ts``）。

级别表由 models.dev v2 站推导（``reasoning.effort`` 全 Claude 系覆盖且
比手写表更全——手写表已退役）；运行时由 pi.dev 全供，故运行期修正
为零，剩余知识只有 compat 默认与新代 Claude 的 mid-convo 旗标组
（对位 pi ``supportsClaudeMidConvoSystemMessages`` /
``supportsClaudeMidConvoEffort`` 的 per-model 规则）。
"""

from nova_protocol import KnownApi

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

DEF = define_provider(
    id="anthropic",
    name="Anthropic",
    base_url="https://api.anthropic.com",
    api=KnownApi.ANTHROPIC_MESSAGES,
    compat_defaults={
        "force_adaptive_thinking": True,
        "supports_strict_tools": True,
    },
    model_corrections={
        "claude-opus-4-8*": {"compat": dict(_MID_CONVO_SYSTEM)},
        "claude-opus-5*": {"compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT}},
        "claude-sonnet-5-5*": {"compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT}},
        "claude-haiku-5-5*": {"compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT}},
        "claude-fable-5*": {"compat": dict(_MID_CONVO_SYSTEM)},
        "claude-fable-5-1*": {
            "compat": {**_MID_CONVO_SYSTEM, **_MID_CONVO_EFFORT},
        },
    },
)
