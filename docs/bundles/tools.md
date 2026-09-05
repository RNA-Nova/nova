# 工具开发（Tool）

LLM 工具是模型可调用的能力单元。**工具即代码**：`backend/tools/<name>.py` 单文件（推荐），需要同目录资产时用 `backend/tools/<name>/executor.py` 目录形态。文件名即工具名。

## 最小工具

```python
# backend/tools/hello.py
from typing import Any, Dict, Optional

from nova_agent import AgentToolResult
from nova_ai import AbortSignal, TextContent
from nova_harness.core.types.resources.tools import (
    NULL_TOOL_EXEC_CONTEXT,
    ToolContext,
    ToolExecContext,
)


class Tool:
    name = "hello"
    description = "向某人问好。用于演示最小工具形态。"
    parameters = {
        "type": "object",
        "properties": {
            "who": {"type": "string", "description": "要问好的对象"},
        },
        "required": ["who"],
    }

    def __init__(self, context: ToolContext):
        self._context = context

    async def execute(
        self,
        tool_call_id: str,
        params: Dict[str, Any],
        signal: Optional[AbortSignal] = None,
        on_update=None,
        ctx: ToolExecContext = NULL_TOOL_EXEC_CONTEXT,
    ):
        who = params["who"]
        return AgentToolResult(
            content=[TextContent(type="text", text=f"你好，{who}！")],
            details={"who": who},
        )
```

声明进 `pyproject.toml` 的 `tools = ["./backend/tools/hello.py"]`，`nova-pkg install` 后即可用。

## 类契约

### 元数据（类属性）

| 属性 | 必需 | 说明 |
|------|------|------|
| `name` | ✓ | 工具名（与文件名一致） |
| `description` | ✓ | 给模型看的功能描述——写清**什么时候用、什么时候别用** |
| `parameters` | ✓ | JSON Schema（模型侧参数校验；框架还会经 jsonschema 再校验一次） |
| `label` | | UI 显示名（缺省用 name） |
| `prompt_snippet` | | 系统提示词里的单行简介（缺省取 description） |
| `prompt_guidelines` | | 该工具的提示词使用守则（多行，进系统提示词） |
| `execution_mode` | | `"parallel"`（默认，可与其他工具并发）/ `"sequential"`（串行——要弹窗问用户的工具用它，如 question） |
| `prepare_arguments` | | `fn(params) -> params`：执行前参数整形（路径归一等） |

### 构造期：`__init__(self, context: ToolContext)`

`ToolContext` 是**构造期不变量**：

- `context.cwd`——会话工作目录；
- `context.settings`——settings 只读视图（活视图，值随设置变化）。

构造期**不做** I/O、不连网络（加载即构造，装得慢=启动慢）。

### 执行期：`execute(...)` 五参

```python
async def execute(self, tool_call_id, params, signal, on_update, ctx): ...
```

| 参数 | 说明 |
|------|------|
| `tool_call_id` | 本次调用的 ID（日志/关联用） |
| `params` | 模型给的参数（已过 jsonschema 校验） |
| `signal` | `AbortSignal`——用户 Esc 时置位；长任务**必须**轮询 `signal.aborted` 并尽快返回 |
| `on_update` | 流式回调 `on_update(AgentToolResult)`——长任务进度（bash 的滚动输出）；结果会实时渲染到工具卡片 |
| `ctx` | `ToolExecContext`——执行期上下文（每次调用现取，见下） |

### `ToolExecContext`（执行期现取注入）

- `ctx.model`——当前模型（需要模型能力的工具用）；
- `ctx.agents`——会话 agents 注册表快照（委派类工具用，见官方 subagent）；
- `ctx.has_ui` / `ctx.ui`——执行期 UI 句柄：
  - `has_ui=False`（headless/print 模式）时一切交互路径必须降级；
  - `ctx.ui.request(method, params)` / `ctx.ui.notify(...)`——反向原语，弹确认/选择/表单（方法名为自由字符串，词汇由包自定；官方基线词汇见 `nova_base.ui_primitives`：`select` / `confirm` / `input` / `form` / `notify_message` / `set_status`）；
  - `ctx.ui.has_capability("dialog:my-dialog")`——前端注册了包侧对话框时为真，走自定义 UI；否则降级到基线原语两步走（官方 question 工具即此双路径范例）。

### 返回值：`AgentToolResult`

```python
AgentToolResult(
    content=[TextContent(type="text", text="给模型看的文本")],  # 也可放 ImageContent
    details={"path": path, "truncated": False},  # 结构化数据——前端渲染器吃这个
    is_error=False,                               # True 时模型会按错误处理（可触发重试）
)
```

- `content` 给模型；`details` 给前端渲染器（平铺结构化数据，**不含渲染形状**——渲染归前端）；
- 错误也是返回值（`is_error=True` + 人话错误文本），不是异常——模型需要读懂错误并自我纠正；
- 长输出自觉截断（参考官方工具的 `truncate_head`，50KB 量级）。

## 纪律（官方工具同款）

1. **中断是头等功能**：任何循环/等待点都查 `signal.aborted`，被取消时返回 `is_error=True` 的 aborted 结果；
2. **路径归一经 helpers**：相对路径相对 `context.cwd` 解析；越权访问（写保护路径）由框架/扩展层拦截，工具不重复造闸；
3. **二进制定位加速**：`resolve_binary("fd")` 三级解析，缺失降级纯 Python——官方 grep/find 的三级链（fd → rg → Python）是模板；
4. **UI 不假设存在**：`has_ui` 先判；`ui` 请求在 headless 下安全 no-op；
5. **输出面向模型写**：错误文本要让模型知道下一步怎么办（"目录不存在：path" 比 "ENOENT" 有用）。

## 测试

官方包的一文件一测即模板（`bundles/nova_coding_agent/backend/tests/tools/test_ls.py`）：

```python
tool = Tool(ToolContext(cwd=str(tmp_path), settings=...))
result = await tool.execute("tc1", {"path": "."}, None, None, NULL_TOOL_EXEC_CONTEXT)
assert not result.is_error
```

## 用户工具（UserTool）——人类触发的姊妹类目

`backend/user_tools/<name>.py` 暴露 `UserTool` 类——元数据同为类属性（import 即可读，白名单/碰撞检测无需会话），`__init__(session)` 注入会话上下文。与 LLM 工具的区别：**由人触发**（如官方 `!` bash），不经模型。可选 `MESSAGE_TYPES` 类属性把自定义消息类型注册进回载注册表（包缺席时旧会话中该类型消息降级为不透明消息，数据不丢）。

下一页：[扩展开发](extensions.md)。
