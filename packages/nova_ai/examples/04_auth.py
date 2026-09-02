"""04 - Auth 解析链

`Models` 的 auth 解析优先级（与 TS 对齐）：
    1. 调用方 options.api_key 显式覆盖
    2. credential store 里已存储的 credential（OAuth 过期时在锁内刷新）
    3. 环境变量（每个 provider 对应固定的环境变量名）

本例演示：
- 环境变量解析（`get_env_api_key`）
- options.api_key 显式覆盖优先于环境变量
- 动态 key 注入（`SimpleStreamOptions.api_key`，适合短寿命 token）

运行：
    python examples/04_auth.py
"""

import os

from nova_ai import SimpleStreamOptions
from nova_ai.utils.env import get_env_api_key


def demo_env_resolution():
    """每个 provider 有固定的环境变量名映射。"""
    for provider in ["volcengine", "moonshotai", "kimi-coding", "openai"]:
        key = get_env_api_key(provider)
        print(f"[env] {provider}: {'已配置' if key else '未配置'}")


def demo_override_precedence():
    """options.api_key 显式给定时，环境变量被忽略。"""
    os.environ["VOLCENGINE_API_KEY"] = "env-key"

    options = SimpleStreamOptions(api_key="explicit-key")
    # stream_simple 的 auth 链第一步就用 options.api_key —— env 不会生效
    assert options.api_key == "explicit-key"
    print("[override] options.api_key 优先于环境变量:", options.api_key)

    # 动态注入：每次调用前解析 key（OAuth 短寿命 token 的典型用法）
    def get_api_key(provider: str):
        return f"fresh-token-for-{provider}"

    dynamic_key = get_api_key("volcengine")
    print("[override] 动态注入的 key:", dynamic_key)


if __name__ == "__main__":
    demo_env_resolution()
    demo_override_precedence()
