"""
示例扩展：给 Agent 增加一个查询天气的工具，并拦截用户输入里的快捷命令。

使用方式（任选其一）：
1. 复制到项目级扩展目录：<cwd>/.nova/extensions/weather_extension.py
2. 复制到全局扩展目录：~/.nova/agent/extensions/weather_extension.py
3. 在 Settings.extensions 里添加本文件绝对路径

下次启动 AgentSession 时，扩展会自动加载。
"""

from nova_agent import AgentToolResult
from nova_ai import TextContent

from nova_harness.core.agent_session.extensions import NovaExtensionAPI
from nova_harness.types.extensions import ExtensionToolDefinition


async def get_weather(ctx, tool_call_id, params, signal):
    """
    扩展工具的 execute 签名固定为：
    (context, tool_call_id, params, signal)
    """
    city = params.get("city", "北京")
    # 这里可以换成真实天气 API；示例直接返回模拟数据
    return f"{city} 今天多云，气温 18-26℃。"


def extension(nova: NovaExtensionAPI):
    # 1. 注册一个自定义工具
    nova.register_tool(
        ExtensionToolDefinition(
            name="get_weather",
            description="查询指定城市的天气",
            parameters={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "城市名称，例如：北京、上海",
                    }
                },
                "required": ["city"],
            },
            execute=get_weather,
        )
    )

    # 2. 监听会话启动事件：自动在系统里打个标记
    def on_session_start(event):
        print(f"[weather_extension] 会话启动：{event.reason}")

    nova.on("session_start", on_session_start)

    # 3. 拦截用户输入：把 /weather 北京 转换成自然语言问题
    def on_input(event):
        text = event.text
        if text.startswith("/weather "):
            city = text.replace("/weather ", "").strip()
            # action="transform" 表示把输入改写后继续走正常流程
            from nova_harness.types.events import InputEventResult

            return InputEventResult(
                action="transform",
                text=f"请帮我查一下 {city} 今天的天气。",
            )
        # action="continue" 表示不处理，交给默认流程
        from nova_harness.types.events import InputEventResult

        return InputEventResult(action="continue")

    nova.on("input", on_input)
