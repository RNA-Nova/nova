"""内置 provider 构建定义注册表（对位 pi ``catalog/providers/index.ts``）。

新增 provider：在本目录加一家定义文件，并在此登记。
"""

from .anthropic import DEF as ANTHROPIC
from .kimi_coding import DEF as KIMI_CODING
from .moonshotai import DEF as MOONSHOTAI
from .moonshotai_cn import DEF as MOONSHOTAI_CN
from .openrouter import DEF as OPENROUTER
from .volcengine import DEF as VOLCENGINE

PROVIDERS = (
    ANTHROPIC,
    KIMI_CODING,
    MOONSHOTAI,
    MOONSHOTAI_CN,
    OPENROUTER,
    VOLCENGINE,
)

__all__ = ["PROVIDERS"]
