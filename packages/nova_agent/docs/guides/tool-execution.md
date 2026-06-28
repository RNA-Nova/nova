# 工具执行机制

`nova_agent` 的每次 assistant turn 可能包含多个 tool call。框架支持两种执行模式，并允许工具自行决定执行方式。

## 执行模式

### parallel（默认）

- 先**顺序**完成每个 tool call 的 prepare 阶段（参数校验、`before_tool_call` hook）。
- 然后**并发**执行所有允许并行的工具。
- `tool_execution_end` 事件按工具实际完成顺序发出。
- 最终 `toolResult` 消息按 assistant 消息里的工具顺序发出。

适合大多数无依赖的工具调用。

### sequential

- 一个工具执行完、finalize 后，再开始下一个。

适合有依赖关系或需要串行执行的工具。

## 配置执行模式

全局配置：

```python
agent = Agent(tool_execution="sequential")
```

单个工具覆盖：

```python
class StepByStepTool(AgentTool):
    execution_mode: str = "sequential"
```

只要当前 batch 里有一个工具是 `sequential`，整个 batch 都会串行执行。

## 工具终止语义

工具结果可以携带 `terminate=True`：

```python
return AgentToolResult(
    content=[TextContent(text="最终答案")],
    terminate=True,
)
```

但**只有当 batch 里所有工具结果都设置了 `terminate=True` 时**，agent 才会跳过自动 follow-up LLM 调用。

如果只有一个工具返回 `terminate=True`，其它没有，则循环继续正常走。

## 参数预处理 `prepare_arguments`

如果模型生成的原始参数需要在做 JSON Schema 校验前先转换一下，可以在工具里实现 `prepare_arguments`：

```python
class DateTool(AgentTool):
    name: str = "get_date"
    parameters: dict = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "format": "date"},
        },
        "required": ["date"],
    }

    def prepare_arguments(self, args):
        # 把 "2026/06/18" 转成 "2026-06-18"
        raw = args.get("date", "")
        return {**args, "date": raw.replace("/", "-")}
```

执行顺序：

```text
raw arguments
    ↓
prepare_arguments
    ↓
validate_tool_arguments (JSON Schema 校验)
    ↓
before_tool_call hook
    ↓
execute
```

`prepare_arguments` 返回的对象必须仍然满足 `parameters` 定义的 schema。

## 常见流程

```text
assistant: [toolCall A, toolCall B]
    ↓
prepare A → prepare B
    ↓
execute A │ execute B   (parallel)
    ↓
tool_execution_end A / B
    ↓
toolResult A, toolResult B
    ↓
下一轮 assistant 响应
```
