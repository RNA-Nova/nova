"""
API注册表
管理不同API类型的提供者
"""

from typing import Dict, Optional, List, Callable, Awaitable, Union, Protocol
from dataclasses import dataclass
from ..core.enums import Api, KnownApi
from ..models import Model
from ..core.messages import Context
from ..streaming.event_stream import AssistantMessageEventStream
from ..utils.stream_options import StreamOptions, SimpleStreamOptions

class ApiProvider(Protocol):
    """
    API提供者接口协议
    
    每个API类型（如openai-completions, anthropic-messages等）都需要注册一个提供者
    """
    
    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[StreamOptions] = None
    ) -> AssistantMessageEventStream:
        """流式调用"""
        ...
    
    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None
    ) -> AssistantMessageEventStream:
        """简化的流式调用"""
        ...


@dataclass
class ApiProviderRecord:
    """API提供者记录"""
    api: str
    stream: Callable
    stream_simple: Callable


class ApiProviderRegistry:
    """API提供者注册表"""
    
    def __init__(self):
        self._providers: Dict[str, ApiProviderRecord] = {}
    
    def register(self, provider: Union[ApiProviderRecord, dict]) -> None:
        """
        注册API提供者
        
        Args:
            provider: 提供者记录，可以是ApiProviderRecord或包含api, stream, stream_simple的字典
        """
        if isinstance(provider, dict):
            provider = ApiProviderRecord(
                api=provider["api"],
                stream=provider["stream"],
                stream_simple=provider["stream_simple"]
            )
        self._providers[provider.api] = provider
    
    def get(self, api: Union[Api, str]) -> Optional[ApiProviderRecord]:
        """
        获取API提供者
        
        Args:
            api: API类型
            
        Returns:
            提供者记录，如果未注册则返回None
        """
        api_str = api.value if hasattr(api, 'value') else api
        return self._providers.get(api_str)
    
    def list(self) -> List[str]:
        """列出所有已注册的API类型"""
        return list(self._providers.keys())
    
    def unregister(self, api: Union[Api, str]) -> Optional[ApiProviderRecord]:
        """
        注销API提供者
        
        Args:
            api: API类型
            
        Returns:
            被注销的提供者记录，如果未注册则返回None
        """
        api_str = api.value if hasattr(api, 'value') else api
        return self._providers.pop(api_str, None)
    
    def has_provider(self, api: Union[Api, str]) -> bool:
        """检查是否已注册指定API的提供者"""
        api_str = api.value if hasattr(api, 'value') else api
        return api_str in self._providers
    
    def clear(self) -> None:
        """清空所有注册的提供者"""
        self._providers.clear()


# 全局注册表实例
_registry = ApiProviderRegistry()


def register_api_provider(provider: Union[ApiProviderRecord, dict]) -> None:
    """
    注册API提供者（便捷函数）
    
    Args:
        provider: 提供者记录
    """
    _registry.register(provider)


def get_api_provider(api: Union[Api, str]) -> Optional[ApiProviderRecord]:
    """
    获取API提供者（便捷函数）
    
    Args:
        api: API类型
        
    Returns:
        提供者记录，如果未注册则返回None
    """
    return _registry.get(api)


def list_api_providers() -> List[str]:
    """列出所有已注册的API类型（便捷函数）"""
    return _registry.list()


def unregister_api_provider(api: Union[Api, str]) -> Optional[ApiProviderRecord]:
    """
    注销API提供者（便捷函数）
    
    Args:
        api: API类型
        
    Returns:
        被注销的提供者记录
    """
    return _registry.unregister(api)


def has_api_provider(api: Union[Api, str]) -> bool:
    """
    检查是否已注册指定API的提供者
    
    Args:
        api: API类型
        
    Returns:
        是否已注册
    """
    return _registry.has_provider(api)


def clear_api_providers() -> None:
    """清空所有注册的API提供者"""
    _registry.clear()


__all__ = [
    "ApiProvider",
    "ApiProviderRecord",
    "ApiProviderRegistry",
    "register_api_provider",
    "get_api_provider",
    "list_api_providers",
    "unregister_api_provider",
    "has_api_provider",
    "clear_api_providers",
]