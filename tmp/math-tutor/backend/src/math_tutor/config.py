"""配置模块

环境变量：
    NOVA_MATH_MODEL_PROVIDER: provider id，默认 volcengine
    NOVA_MATH_MODEL_ID: model id，默认 deepseek-v3-2-251201
    NOVA_MATH_API_KEY: 直接传入的 API key（可选，也可使用环境变量 VOLCENGINE_API_KEY 等）
    NOVA_MATH_PORT: 服务端口，默认 8000
    NOVA_MATH_FRONTEND_DIST: 前端构建产物路径
"""

import os


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


# Provider 与模型配置
DEFAULT_PROVIDER = env("NOVA_MATH_MODEL_PROVIDER", "volcengine")
DEFAULT_MODEL_ID = env("NOVA_MATH_MODEL_ID", "deepseek-v3-2-251201")

# API Key 优先使用 NOVA_MATH_API_KEY，否则走 nova_ai 的环境变量解析
DEFAULT_API_KEY = env("NOVA_MATH_API_KEY")

# 服务器
DEFAULT_PORT = int(env("NOVA_MATH_PORT", "8000"))
DEFAULT_HOST = env("NOVA_MATH_HOST", "0.0.0.0")

# 前端构建产物路径
_default_dist = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "frontend", "dist"
)
FRONTEND_DIST = os.path.abspath(
    env("NOVA_MATH_FRONTEND_DIST", _default_dist)
)
