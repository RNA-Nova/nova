"""
示例扩展：拦截用户输入里的快捷命令并监听会话事件。

使用方式（任选其一）：
1. 复制到项目级扩展目录：<cwd>/.nova/extensions/weather_extension.py
2. 复制到全局扩展目录：~/.nova/agent/extensions/weather_extension.py
3. 在 Settings.extensions 里添加本文件绝对路径

下次启动 AgentSession 时，扩展会自动加载。
"""

from nova_harness.core.extensions import NovaExtensionAPI
from nova_harness.core.types.events import InputEventResult


def extension(nova: NovaExtensionAPI):
    # 1. 监听会话启动事件：自动在系统里打个标记
    def on_session_start(event):
        print(f"[weather_extension] 会话启动：{event.reason}")

    nova.on("session_start", on_session_start)

    # 2. 拦截用户输入：把 /weather 北京 转换成自然语言问题
    def on_input(event):
        text = event.text
        if text.startswith("/weather "):
            city = text.replace("/weather ", "").strip()
            # action="transform" 表示把输入改写后继续走正常流程
            return InputEventResult(
                action="transform",
                text=f"请帮我查一下 {city} 今天的天气。",
            )
        # action="continue" 表示不处理，交给默认流程
        return InputEventResult(action="continue")

    nova.on("input", on_input)

    # 3. 注册一个 slash 命令
    nova.registerCommand(
        "weather",
        {
            "description": "查询天气（示例命令）",
            "handler": lambda args, ctx: print(f"[weather_extension] 命令参数：{args}"),
        },
    )
