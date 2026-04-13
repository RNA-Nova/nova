"""
路由配置
用于OpenRouter和Vercel AI Gateway等代理服务
"""

from typing import Optional, List
from dataclasses import dataclass, field

from mashumaro.mixins.json import DataClassJSONMixin

@dataclass
class OpenRouterRouting(DataClassJSONMixin):
    """
    OpenRouter 提供商路由偏好设置
    控制OpenRouter将请求路由到哪些上游提供商
    
    @see https://openrouter.ai/docs/provider-routing
    """
    # 专门使用的提供商列表（例如 ["amazon-bedrock", "anthropic"]）
    only: Optional[List[str]] = None
    
    # 按顺序尝试的提供商列表（例如 ["anthropic", "openai"]）
    order: Optional[List[str]] = None


@dataclass
class VercelGatewayRouting(DataClassJSONMixin):
    """
    Vercel AI Gateway 路由偏好设置
    控制网关将请求路由到哪些上游提供商
    
    @see https://vercel.com/docs/ai-gateway/models-and-providers/provider-options
    """
    # 专门使用的提供商列表（例如 ["bedrock", "anthropic"]）
    only: Optional[List[str]] = None
    
    # 按顺序尝试的提供商列表（例如 ["anthropic", "openai"]）
    order: Optional[List[str]] = None