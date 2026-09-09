"""API 协议实现模块

每个子包对应一种 API 协议，导出 ``stream`` / ``stream_simple`` 两个函数，
直接满足 ``ProviderStreams`` 契约（对齐 TS ``src/api/*.ts``）。

惰性导入（对齐 TS ``lazyApi`` 的包体收益）：协议实现连带的重依赖
（openai SDK 等）只在真正访问时加载——裸 ``import nova_ai`` 或仅使用
``_shared`` 共享件的路径不再为它们买单。实现模块经本 ``__getattr__``
首次访问时物化，之后缓存为普通模块属性。
"""

from typing import Any

_LAZY_MODULES = {
    "openai_completions": "nova_ai.api_impls.openai_completions",
}

_LAZY_NAMES = {
    # openai_completions 门面再导出
    "stream": "openai_completions",
    "stream_simple": "openai_completions",
    "OpenAICompletionsOptions": "openai_completions",
    "ProviderStreamOptions": "openai_completions",
    "detect_compat": "openai_completions",
    "get_compat": "openai_completions",
    "build_params": "openai_completions",
}


def __getattr__(name: str) -> Any:
    target = _LAZY_MODULES.get(name)
    if target is not None:
        import importlib

        module = importlib.import_module(target)
        globals()[name] = module
        return module
    if name in _LAZY_NAMES:
        import importlib

        module = importlib.import_module(_LAZY_MODULES[_LAZY_NAMES[name]])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list:
    return sorted(set(globals()) | set(_LAZY_MODULES) | set(_LAZY_NAMES))


__all__ = [
    "stream",
    "stream_simple",
    "OpenAICompletionsOptions",
    "ProviderStreamOptions",
]
