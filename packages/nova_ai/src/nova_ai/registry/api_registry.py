"""
API 注册表
管理不同 API 协议的适配器
"""

from typing import Dict, Optional, List, Union
from ..types.enums import Api
from ..types.api_adapter import ApiAdapter


class ApiRegistry:
    """API 适配器注册表"""

    def __init__(self):
        self._adapters: Dict[str, ApiAdapter] = {}

    def register(self, adapter: ApiAdapter) -> None:
        """
        注册 API 适配器

        Args:
            adapter: 实现 ApiAdapter Protocol 的对象，包含 api 属性以及
                     stream、stream_simple 方法
        """
        self._adapters[adapter.api] = adapter

    def get(self, api: Union[Api, str]) -> Optional[ApiAdapter]:
        """
        获取 API 适配器

        Args:
            api: API 类型

        Returns:
            适配器对象，如果未注册则返回 None
        """
        api_str = api.value if hasattr(api, 'value') else api
        return self._adapters.get(api_str)

    def list(self) -> List[str]:
        """列出所有已注册的 API 类型"""
        return list(self._adapters.keys())

    def unregister(self, api: Union[Api, str]) -> Optional[ApiAdapter]:
        """
        注销 API 适配器

        Args:
            api: API 类型

        Returns:
            被注销的适配器对象，如果未注册则返回 None
        """
        api_str = api.value if hasattr(api, 'value') else api
        return self._adapters.pop(api_str, None)

    def has_adapter(self, api: Union[Api, str]) -> bool:
        """检查是否已注册指定 API 的适配器"""
        api_str = api.value if hasattr(api, 'value') else api
        return api_str in self._adapters

    def clear(self) -> None:
        """清空所有注册的适配器"""
        self._adapters.clear()


# 全局注册表实例
_api_registry = ApiRegistry()


def register_api_adapter(adapter: ApiAdapter) -> None:
    """注册 API 适配器（便捷函数）"""
    _api_registry.register(adapter)


def get_api_adapter(api: Union[Api, str]) -> Optional[ApiAdapter]:
    """获取 API 适配器（便捷函数）"""
    return _api_registry.get(api)


def list_api_adapters() -> List[str]:
    """列出所有已注册的 API 类型（便捷函数）"""
    return _api_registry.list()


def unregister_api_adapter(api: Union[Api, str]) -> Optional[ApiAdapter]:
    """注销 API 适配器（便捷函数）"""
    return _api_registry.unregister(api)


def has_api_adapter(api: Union[Api, str]) -> bool:
    """检查是否已注册指定 API 的适配器"""
    return _api_registry.has_adapter(api)


def clear_api_adapters() -> None:
    """清空所有 API 适配器"""
    _api_registry.clear()


__all__ = [
    "ApiAdapter",
    "ApiRegistry",
    "register_api_adapter",
    "get_api_adapter",
    "list_api_adapters",
    "unregister_api_adapter",
    "has_api_adapter",
    "clear_api_adapters",
]
