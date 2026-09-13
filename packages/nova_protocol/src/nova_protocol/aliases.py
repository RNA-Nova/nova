"""跨模块共享的基础类型别名。"""

from typing import Dict, Optional

# Provider 级环境变量覆盖，优先级高于进程环境变量
ProviderEnv = Dict[str, str]

# 请求头；值为 None 表示抑制同名的 provider/API 默认头
ProviderHeaders = Dict[str, Optional[str]]

__all__ = ["ProviderEnv", "ProviderHeaders"]
