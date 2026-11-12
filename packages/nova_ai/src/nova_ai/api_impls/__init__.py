"""API 协议实现模块

每个模块或子包对应一种 API 协议；聊天实现导出 ``stream`` /
``stream_simple``，图像与分类实现导出各自的非流式入口（对齐 TS
``src/api/*.ts``）。

惰性导入（对齐 TS ``lazyApi`` 的包体收益）：协议实现连带的重依赖
（openai/anthropic SDK 等）只在真正访问时加载——裸 ``import nova_ai``
或仅使用 ``_shared`` 共享件的路径不再为它们买单。实现模块经本
``__getattr__`` 首次访问时物化，之后缓存为普通模块属性。
"""

from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from .anthropic_messages import AnthropicOptions, stream, stream_simple
    from .openai_completions import OpenAICompletionsOptions

    ProviderStreamOptions = Union[OpenAICompletionsOptions, AnthropicOptions]

_LAZY_MODULES = {
    "openai_completions": "nova_ai.api_impls.openai_completions",
    "anthropic_messages": "nova_ai.api_impls.anthropic_messages",
    "openrouter_images": "nova_ai.api_impls.openrouter_images",
    "typesafe_system_one": "nova_ai.api_impls.typesafe_system_one",
}

# 根门面只承载明确的选项类型；调用一律经具体协议模块，避免把
# stream/build_params 等通用词误当成可跨协议自动分派的历史别名。
_LAZY_NAMES = {
    "OpenAICompletionsOptions": "openai_completions",
    "AnthropicOptions": "anthropic_messages",
}


def __getattr__(name: str) -> Any:
    import importlib

    if name == "ProviderStreamOptions":
        # 全部实现选项的联合（单一正典源归本门面——两个 options 模块都是
        # 纯 dataclass 轻模块，但按需物化保持一致性）
        from .anthropic_messages.options import AnthropicOptions
        from .openai_completions.options import OpenAICompletionsOptions

        value = Union[OpenAICompletionsOptions, AnthropicOptions]
        globals()[name] = value
        return value
    target = _LAZY_MODULES.get(name)
    if target is not None:
        module = importlib.import_module(target)
        globals()[name] = module
        return module
    if name in _LAZY_NAMES:
        module = importlib.import_module(_LAZY_MODULES[_LAZY_NAMES[name]])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(
        set(globals())
        | set(_LAZY_MODULES)
        | set(_LAZY_NAMES)
        | {"ProviderStreamOptions"}
    )


__all__ = [
    "OpenAICompletionsOptions",
    "AnthropicOptions",
    "ProviderStreamOptions",
]
