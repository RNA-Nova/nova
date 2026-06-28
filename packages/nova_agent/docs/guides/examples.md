# 使用示例

本文档展示 `nova_agent` 的常见使用场景。

---

## 目录

1. [基础对话](#基础对话)
2. [带工具的 Agent](#带工具的-agent)
3. [事件订阅](#事件订阅)
4. [Steering 插话](#steering-插话)
5. [Follow-up 续跑](#follow-up-续跑)
6. [Abort 取消](#abort-取消)
7. [自定义 convert_to_llm](#自定义-convert_to_llm)

---

## 基础对话

```python
import asyncio
from nova_agent import Agent

async def main():
    agent = Agent()
    agent.set_model(...)  # 设置 nova_ai Model
    await agent.prompt("你好")
    print(agent.state.messages[-1].content[0].text)

asyncio.run(main())
```

---

## 带工具的 Agent

```python
from nova_ai import TextContent
from nova_agent import AgentTool, AgentToolResult

class EchoTool(AgentTool):
    name: str = "echo"
    description: str = "Echo the input"
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

async def main():
    agent = Agent()
    agent.set_model(...)
    agent.set_tools([EchoTool()])
    await agent.prompt("调用 echo，参数 message=hello")

asyncio.run(main())
```

---

## 事件订阅

```python
def listener(event):
    if event.type == "message_update":
        print(".", end="", flush=True)
    elif event.type == "tool_execution_end":
        print(f"\n工具 {event.tool_name} 完成，is_error={event.is_error}")

agent.subscribe(listener)
```

---

## Steering 插话

```python
async def main():
    task = asyncio.create_task(agent.prompt("请详细介绍一下 Python"))
    await asyncio.sleep(1)
    agent.steer(UserMessage(content="停止，简要回答即可"))
    await task
```

---

## Follow-up 续跑

```python
agent.follow_up(UserMessage(content="再举两个例子"))
await agent.prompt("介绍一下 asyncio")
```

`follow_up` 的消息会在当前 run 自然结束后注入。

---

## Abort 取消

```python
async def main():
    task = asyncio.create_task(agent.prompt("写一个长故事"))
    await asyncio.sleep(2)
    agent.abort()
    await task
```

---

## 自定义 convert_to_llm

过滤掉某些自定义消息类型：

```python
def convert(messages):
    return [m for m in messages if m.role in ("user", "assistant", "toolResult")]

agent = Agent(convert_to_llm=convert)
```
