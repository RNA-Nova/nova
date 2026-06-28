# 快速开始

本文介绍如何创建一个带工具的 `nova_agent.Agent` 并发起对话。

## 安装

`nova_agent` 依赖 `nova_ai`，请确保两者都在同一 Python 环境中：

```bash
cd packages/nova_ai && pip install -e .
cd packages/nova_agent && pip install -e .
```

## 定义工具

```python
from nova_ai import TextContent
from nova_agent import AgentTool, AgentToolResult

class EchoTool(AgentTool):
    name: str = "echo"
    description: str = "Echo the input message"
    parameters: dict = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }
    label: str = "Echo"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"echo: {params['message']}")],
            details={},
        )
```

## 创建 Agent 并对话

```python
import asyncio
from nova_agent import Agent

async def main():
    agent = Agent()
    agent.set_model(...)          # 设置 nova_ai Model
    agent.set_tools([EchoTool()])

    agent.subscribe(lambda e: print(e.type))

    await agent.prompt("请调用 echo 工具，参数 message=hello")
    print(agent.state.messages[-1])

asyncio.run(main())
```

## 事件订阅

```python
def listener(event):
    if event.type == "message_end":
        print(event.message)

agent.subscribe(listener)
```

## 常用 hook

```python
async def before(ctx, signal):
    if ctx.tool_call.name == "dangerous_cmd":
        return {"block": True, "reason": "禁止执行危险命令"}

async def after(ctx, signal):
    # 覆盖结果内容
    return {"content": [TextContent(text="已处理")]}

agent = Agent(
    before_tool_call=before,
    after_tool_call=after,
)
```

## 下一步

- 了解 [hooks](hooks.md) 完整列表和最佳实践。
- 阅读 [架构设计](../architecture-design.md) 理解内部循环。
