# nova-agent

事件驱动的异步 LLM Agent 框架：状态管理、工具执行、消息队列与生命周期控制，构建在 [`nova-ai`](../nova_ai) 的多厂商模型抽象之上。

## 特性

- **完整的 Agent 循环**：用户消息 → LLM 流式响应 → 工具调用 → 工具结果 → 下一轮，直到没有工具调用也没有排队消息。
- **事件驱动**：agent / turn / message / tool 四个层级的十种事件，订阅者按注册顺序被逐个 `await`，天然支持流式 UI。
- **工具系统**：JSON Schema 参数校验与类型矫正、并行/串行执行模式、执行进度回调、`before`/`after` 拦截钩子。
- **消息队列**：steering（运行中插队）与 follow-up（收尾后续跑）双队列，注入时机明确。
- **生命周期控制**：`AbortSignal` 取消传播到 provider 流与工具执行；`continue_()` 支持失败重试与队列续跑。
- **统一运行句柄**：`prompt()` / `continue_()` 同步返回 `AgentRun`，提供 `task`、`events`、`signal` 与 `abort()`；`await run` 等待完整 settle，`run.events` 是权威事件流。

要求 Python `>=3.13,<3.14`。

## 安装

```bash
pip install nova-agent nova-ai
```

本包 PyPI 名为 `nova-agent`，import 名为 `nova_agent`；跨组件类型（`Model`、消息、流式事件等）由 `nova-protocol` 提供，取消词汇（`AbortSignal` 等）由 `nova-async` 提供，流式调用原语由 `nova-ai` 提供，几个包需在同一环境中。

仓库内开发安装（monorepo 根目录统一环境）：

```bash
pixi install --environment dev
```

## 快速上手

下面这段代码使用 mock `stream_fn`，不依赖任何 API Key，可以直接运行：

```python
import asyncio

from nova_agent import Agent

from nova_ai import EventStream
from nova_protocol import (
    AssistantMessage,
    DoneEvent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    StartEvent,
    TextContent,
)

model = Model(
    id="demo",
    name="demo",
    api=KnownApi.OPENAI_COMPLETIONS,
    provider=KnownProvider.OPENAI,
    base_url="https://example.com",
    reasoning=False,
    input_types=["text"],
    cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
    context_window=8192,
    max_tokens=4096,
)


def mock_stream_fn(m, context, options):
    """离线 mock：固定回复一条文本。接真实模型时无需自己实现，见下文。"""
    message = AssistantMessage(
        role="assistant",
        content=[TextContent(text="你好，我是 Agent。")],
        api=m.api,
        provider=m.provider,
        model=m.id,
        stop_reason="stop",
    )
    stream = EventStream(
        is_complete=lambda e: e.type == "done",
        extract_result=lambda e: e.message,
    )
    stream.push(StartEvent(partial=message))
    stream.push(DoneEvent(reason="stop", message=message))
    stream.end()
    return stream


async def main():
    agent = Agent(
        initial_state={
            "system_prompt": "你是一个简洁的助手。",
            "model": model,
        },
        stream_fn=mock_stream_fn,
    )

    # 订阅事件（同步或异步函数均可），返回退订函数
    unsubscribe = agent.subscribe(lambda event, signal: print(f"event: {event.type}"))

    await agent.prompt("你好")

    print("助手回复:", agent.state.messages[-1].content[0].text)
    unsubscribe()


asyncio.run(main())
```

接入真实模型时 `stream_fn` 有两级解析（对齐 pi）：**构造参数 → `set_default_stream_fn()` 注册的全局函数**。两级都缺席时构造即抛错（fail-fast，无静默兜底——避免"忘了配"变成悄悄用环境变量凭据发真实请求）：

```python
from nova_ai import builtin_models, get_builtin_model

# 库消费者：显式注入（推荐示范）
agent = Agent(stream_fn=builtin_models().stream_simple)
model = get_builtin_model("volcengine", "deepseek-v4-flash-260425")
assert model is not None
agent.set_model(model)
await agent.prompt("你好")  # 需要 VOLCENGINE_API_KEY 环境变量
```

应用宿主可在启动时注册一次全局默认，之后各处省略（harness 即走此路径）：

```python
from nova_agent import set_default_stream_fn
from nova_ai import builtin_models

set_default_stream_fn(builtin_models().stream_simple)  # 鉴权从环境变量解析
```

## 核心概念

### AgentMessage 与 LLM 消息

`AgentMessage = Union[Message, CustomAgentMessage]`（开放集：判别联合 + 兜底成员 + `union_mode="left_to_right"`）。其中 `Message` 是 `nova_protocol` 的判别联合（判别键 `role`）：

- `SystemMessage`：系统提示词与工具状态变更，携带 `content`、可选的 `sections`、`tools_added` / `tools_removed`；初始 prompt/tools 与对话中途的变更都记录在转录里；
- `UserMessage`：用户输入，`content` 为字符串或 `TextContent` / `ImageContent` 列表；
- `AssistantMessage`：助手回复，`content` 为 `TextContent` / `ThinkingContent` / `ToolCall` 列表，附带 `usage`、`stop_reason`、`error_message` 等元数据；
- `ToolResultMessage`：工具结果，携带 `tool_call_id`、`tool_name`、`content`、`details`、`usage`、`duration_ms`、`is_error`。`structured_content` 只存在执行结果 / `AgentToolCallOutcome.result`，不进入转录。

`CustomAgentMessage` 是留给应用的扩展基类（见「自定义消息类型」）。

### 上下文如何进入 LLM

每次调用 LLM 前，上下文经过两段管线：

```
AgentMessage[] → transform_context() → AgentMessage[] → convert_to_llm() → Message[] → LLM
                      （可选）                                  （每次必走）
```

1. `transform_context(messages, signal)`：可选的异步变换，适合做上下文窗口裁剪、注入外部信息；
2. `convert_to_llm(messages)`：把 `AgentMessage` 转成 LLM 可理解的消息。默认实现保留 `system` / `user` / `assistant` / `toolResult` 四种 role，其余一律过滤——自定义消息类型必须在这里转换或丢弃。

转换结果直接组成 `nova_protocol.Context(messages=...)`，交给 `stream_fn(model, context, options)` 调用。系统提示词和工具声明不再另行拼接：provider 从转录重放当前系统提示词与工具集，因此会话恢复和分支导航能忠实还原当时的指令状态。

## 事件流

所有状态变更都通过事件广播。理解事件序列是构建响应式 UI 的基础。

### prompt() 事件序列（无工具）

```
prompt("你好")
├─ agent_start
├─ turn_start
├─ message_start    { message: UserMessage }          # 你的输入
├─ message_end      { message: UserMessage }
├─ message_start    { message: AssistantMessage }     # LLM 开始流式回复
├─ message_update   { message: 部分消息, ... }         # 流式增量 ×N
├─ message_end      { message: AssistantMessage }     # 完整回复
├─ turn_end         { message, tool_results: [] }
└─ agent_end        { messages: [...] }
```

### 含工具调用的事件序列

助手消息带有 toolCall 时，循环继续：

```
prompt("读取 config.json")
├─ agent_start
├─ turn_start
├─ message_start/end     { UserMessage }
├─ message_start         { AssistantMessage（含 toolCall） }
├─ message_update ×N
├─ message_end           { AssistantMessage }
├─ tool_execution_start  { tool_call_id, tool_name, args }
├─ tool_execution_update { ..., partial_result }       # 工具流式进度（可选）
├─ tool_execution_end    { tool_call_id, result, is_error }
├─ message_start/end     { ToolResultMessage }
├─ turn_end              { message, tool_results: [toolResult] }
│
├─ turn_start                                          # 下一轮：LLM 根据工具结果继续
├─ message_start/end/update ×N
├─ turn_end
└─ agent_end
```

默认并行模式下，同一条 assistant 消息里的多个工具调用：所有 `tool_execution_start` 按提交顺序先发出，`tool_execution_end` 按各工具实际完成顺序发出，`ToolResultMessage` 则按 assistant 消息中的调用顺序落账。

### continue_() 的事件序列

`continue_()` 不新增消息，从当前上下文继续（用于失败重试、消费排队消息）。上下文尾消息必须是 `user` 或 `toolResult`；若尾消息是 `assistant`，则依次尝试消费 steering 队列、follow_up 队列作为新输入，两者皆空时抛 `RuntimeError`。

### 事件类型参考

事件均为不可变的 frozen 模型，字段必填：

| `type` | 说明 | 关键属性 |
|---|---|---|
| `agent_start` | run 开始 | — |
| `agent_end` | run 收尾，之后不再有任何事件 | `messages`：本次 run 新增的全部消息 |
| `turn_start` | 新一轮开始（一次 LLM 调用 + 其工具执行） | — |
| `turn_end` | 本轮完成 | `message`：本轮 assistant 消息；`tool_results`：本轮工具结果消息 |
| `message_start` | 任意消息开始（user / assistant / toolResult） | `message` |
| `message_update` | 仅 assistant：流式增量 | `message`（当前部分消息副本）；`assistant_message_event`（`nova_ai` 增量事件，如 `text_delta`） |
| `message_end` | 消息完成 | `message`（完整消息） |
| `tool_execution_start` | 工具开始执行 | `tool_call_id`、`tool_name`、`args` |
| `tool_execution_update` | 工具流式进度 | 同上 + `partial_result` |
| `tool_execution_end` | 工具执行结束 | 同上 + `result`、`is_error` |

### 权威事件流与独立观察

- `run.events` 是本次 run 的权威、单消费者事件流，包含正常完成、Provider 失败、abort、外部 Task 取消和首步前取消的完整事件序列，包括 `agent_end`。
- `agent.subscribe(fn)` 用于直接 SDK 使用者长期观察多个 run，接受同步或异步函数，签名 `(event, signal)`，返回退订函数。每个观察者独立消费、独立异常和取消；慢或失败的观察者不影响 run。
- 需要“处理完成后才能继续”的消息干预使用 `on_message_end` 执行 hook，不混入观察订阅。Harness 直接消费 `run.events`，不使用观察适配器。

## Run 生命周期与任务所有权【现状】

每次 `prompt()` / `continue_()` 都同步返回独立的 `AgentRun`。活动守卫使用私有运行身份，不依赖公开可写的 `state.is_streaming`；旧 run 的迟到事件与状态归约不得污染新 run。被 abort/replace 接管的 run 不再走正常完成。

每次 run 只有一个公开 settle Task：执行、失败终态补齐、watchdog 清理、状态归约和 active run 清理全部完成后才落定。`await run` 等待同一边界；`run.events.result()` 只表示事件终态并返回本次新消息。直接取消 Task 属于硬取消，仍先发布内部与权威事件流终态，再把 `CancelledError` 交给调用方；首步前取消也由 done callback 同步完成清理。

任务按生命周期分作用域，不要求所有地方共用一本总账：

- **request 级**：provider 自己管理 HTTP、driver 与 transport；Agent 负责消费关系。`DoneEvent` / `ErrorEvent` 到达后不再 abort，只 join；abandonment 先通知 provider 清理，再有界 join，无响应时才硬取消。硬取消后也不无界等待取消抵抗的 driver。
- **tool batch 级**：工具与 update 任务按批归属，串行工具和单次 `run_tool_call` 也有独立取消边界。异常路径先关事件闸，再 `close → cancel_all → 有界 wait`；迟到的工具事件在工具作用域和 Agent run 身份两层被丢弃。

正常终态线性化顺序为：

```text
provider 收尾 → 工具批收尾 → 终态消息与内部归约 → run.events 的 agent_end
→ 按 run 身份清理 → 公开 idle
```

硬取消也须完成内部归约、权威事件流终态和完整 settle。`run.events.result()` 返回不等于 driver/transport 已结束；`close()` 不等于取消，`cancel_all()` 不等于排水，`wait()` 才是排水。session 级后台任务归 harness 的会话属主，不与当前 run 共用跨代台账。

补充行为契约【现状】：工具 update observer 异常转成工具错误结果；观察者异常不影响 run。`turn_end` / `agent_end` 每类终态事件最多一次；执行型 hook 失败只按当前阶段补齐，不重复生成整套生命周期。abort 先协作取消，超过 100ms 未收尾则硬取消并告警。sequential 的临界区为 execute + update 排干 + after（终态产生），`tool_execution_end` 与 toolResult 通知在门外。

## Agent 选项

```python
Agent(
    *,
    initial_state=None,        # AgentState 或 dict
    convert_to_llm=None,       # AgentMessage[] → Message[]（可异步）
    transform_context=None,    # 上下文变换（异步），在 convert_to_llm 之前
    steering_mode="one-at-a-time",
    follow_up_mode="one-at-a-time",
    stream_fn=None,            # (model, context, options) → AssistantMessageEventStream
    session_id=None,           # 透传给 provider 的会话 ID（缓存亲和）
    get_api_key=None,          # (provider) → str | None（可异步），每次 LLM 调用前解析
    thinking_budgets=None,     # nova_ai.ThinkingBudgets(minimal/low/medium/high)
    transport=Transport.AUTO,  # 传输方式
    max_retry_delay_ms=None,   # 单次重试等待上限（毫秒），透传 nova_ai 请求层
    timeout=None,              # 请求超时（秒）
    tool_execution="parallel", # 工具执行模式
    identity=None,             # ClientIdentity：User-Agent 与 provider 归因头
    on_payload=None,           # 请求载荷回调：(params, model_identity)，可返回替换载荷
    on_response=None,          # 响应观测回调：(ProviderResponse, model_identity)
    on_provider_stream_event=None,  # provider 原始流事件观测：(event, model_identity)
    before_tool_call=None,     # 见下
    after_tool_call=None,
    prepare_request=None,
    prepare_next_turn=None,
    finish_turn=None,
)
```

`initial_state` 接受 `AgentState` 实例或 dict，dict 只允许五个键（多传抛 `TypeError`）：

```python
initial_state={
    "system_prompt": "你是一个助手。",
    "model": model,                     # nova_protocol.Model（也可以是 dict，自动校验为 Model）
    "thinking_level": ModelThinkingLevel.MEDIUM,
    "tools": [my_tool],
    "messages": [],
}
```

传入 `AgentState` 时只拷贝这五个配置字段，运行时字段（`is_streaming` 等）一律重置。初始 `system_prompt` / `tools` 会折叠成首条 `SystemMessage`；若 `messages` 已以 system 消息开头，则转录自身优先。

### Hooks

五个 hook 在构造时注入，Agent 层签名均为 `(ctx, signal)`，可同步可异步，返回 `None` 表示不改变默认行为。

**`before_tool_call(ctx, signal)`** — 参数校验通过之后、`execute` 之前调用（此时 `tool_execution_start` 已发出）。`ctx` 字段：`assistant_message`、`tool_call`、`args`（校验矫正后）、`context`。

```python
def before(ctx, signal):
    if ctx.tool_call.name == "bash":
        return BeforeToolCallResult(block=True, reason="bash 未授权", terminate=True)
    return None
```

`block=True` 拦截执行：不调用工具，直接产出 `is_error=True` 的错误 toolResult（文本为 `reason`）。`terminate=True` 进一步要求整批提前终止（终止语义见「工具」一节）。

**`after_tool_call(ctx, signal)`** — 工具执行完成后、`tool_execution_end` 与 toolResult 消息事件发出之前调用。`ctx` 字段：`assistant_message`、`tool_call`、`args`、`result`、`is_error`、`context`。返回 `AfterToolCallResult` 做部分覆盖，未给的字段保持原值：

```python
def after(ctx, signal):
    if not ctx.is_error:
        return AfterToolCallResult(details={**ctx.result.details, "audited": True})
    return None
```

hook 自身抛异常时，结果替换为错误结果（`is_error=True`）。

**`prepare_request(ctx, signal)`** — 每次 provider 请求前运行，包括第一轮。`ctx` 字段为 `context`、`model`、`thinking_level`；返回 `AgentRequestUpdate(context=..., model=..., thinking_level=...)` 替换本次及后续请求运行时，不追加消息。

**`prepare_next_turn(ctx, signal)`** — 仅在已确定会继续时、下一轮 `turn_start` 前运行；自然收尾的最终回合不会调用。`ctx` 字段：`message`、`tool_results`、`context`、`new_messages`、`turn_index`。返回 `AgentLoopTurnUpdate` 替换下一轮运行时：

```python
def prepare(ctx, signal):
    if ctx.turn_index >= 3:
        return AgentLoopTurnUpdate(model=cheaper_model)
    return None  # 不变
```

可替换字段：`context`（整个 `AgentContext`）、`model`、`thinking_level`；`messages` 可在下一轮请求前追加到转录。

**`finish_turn(ctx, signal)`** — 工具完成后、`turn_end` 前运行，`ctx` 与 `prepare_next_turn` 相同。返回 `AgentTurnDecision(action="end")` 优雅结束；返回 `action="continue"` 时，若没有工具结果、steering 或 follow-up 等自然续跑输入，就补一次仅含当前上下文的请求。错误/中止回合仍会调用该 hook 供观测，但始终硬退出，决策被忽略。

> 低层 `AgentLoopConfig` 中只有 `prepare_next_turn` 的签名不同：它只收 `ctx`，signal 由调用方自行持有；其余四个 hook 与 Agent 层同为 `(ctx, signal)`。

## Agent 状态

`agent.state` 是 `AgentState` 实例（普通 class，可变容器）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `system_prompt` | `str` | 只读属性：重放转录中的 system 消息得到当前提示词，默认 `""` |
| `model` | `Model` | 当前模型；未配置时是占位模型（`id="unknown"`），`state.has_configured_model()` 返回 `False`，`prompt()` 会 fail-fast |
| `thinking_level` | `ModelThinkingLevel` | 思考级别，默认 `OFF` |
| `tools` | `List[AgentTool]` | 可用工具 |
| `messages` | `List[AgentMessage]` | 完整转录（含 system、toolResult 与自定义消息） |
| `is_streaming` | `bool` | 是否有 run 在进行 |
| `streaming_message` | `AgentMessage \| None` | 流式期间当前部分 assistant 消息 |
| `pending_tool_calls` | `Set[str]` | 执行中的 `tool_call_id` |
| `error_message` | `str \| None` | 最近一轮 assistant 消息的错误文本 |

`tools` 与 `messages` 赋值时会**拷贝顶层数组**（防止外部引用污染内部状态）；对 getter 返回的数组原地 `append` / `clear` 则直接改当前状态。系统提示词没有第二份可变状态，也不再提供 `set_system_prompt()`；中途变更通过 `SystemMessage` 进入转录。

## 方法

| 方法 | 说明 |
|---|---|
| `prompt(input, images=None)` | 同步创建并返回 `AgentRun`。`input` 为 `str`（可附 `images: List[ImageContent]`，如 `ImageContent(data=<base64>, mime_type="image/jpeg")`）、单条 `AgentMessage` 或 `AgentMessage` 列表；运行中调用、或未配置模型时抛 `RuntimeError` |
| `continue_()` | 同步创建并返回 continuation 的 `AgentRun`；无可继续内容时抛 `RuntimeError` |
| `abort()` | 先协作取消；超过 100ms 未收尾则硬取消并告警 |
| `await wait_for_idle()` | 等待当前 run 完整落定；等待方自身被取消不会传染给 run（`asyncio.shield`） |
| `subscribe(fn)` | 注册独立观察监听器，返回退订函数；慢或失败的观察者不影响 run |
| `set_model(m)` / `set_thinking_level(l)` / `set_tools(ts)` | 状态修改；工具集变化会在下次请求前记录成转录差分 |
| `replace_messages(ms)` / `append_message(m)` / `clear_messages()` | 消息操作（`replace` 拷贝顶层数组） |
| `peek_queued_messages()` | 预览下一条待消费队列消息，不从队列移除 |
| `reset()` | 清空消息、双队列与错误状态，保留当前折叠后的系统提示词与工具基线；运行中调用抛 `RuntimeError` |

`AgentRun` 成员：`task`（完整 settle Task）、`events`（本次权威事件流）、`signal`（本次取消信号）、`abort()`（先协作取消再硬取消）；`await run` 等待完整 settle，`run.events.result()` 获取本次新消息。

属性：`state`、`signal`（当前 run 的 `AbortSignal`，空闲时 `None`）、`session_id`、`thinking_budgets`、`transport`、`steering_mode` / `follow_up_mode`（property，附 `set_/get_` 方法）可读写；`tool_execution`、`stream_fn`、`convert_to_llm`、`transform_context`、`get_api_key`、`max_retry_delay_ms`、`identity`、`on_payload`、`on_response`、`on_provider_stream_event` 与六个 hook 是普通属性，两次 run 之间可直接赋值。

`prompt()` / `continue_()` 对 provider 失败等运行期异常**不向外抛**：框架生成一条 `stop_reason="error"`（已中断则为 `"aborted"`）的 assistant 失败消息，补发 `message_start` / `message_end` / `turn_end` / `agent_end` 后正常收尾，错误文本可从 `state.error_message` 读取。直接取消返回的 Task 仍按取消语义向原调用方抛出 `CancelledError`，但内部与权威事件流终态会先落定。

## Steering 与 Follow-up

两条队列都用 `AgentMessage` 入队，模式为 `"one-at-a-time"`（默认，每次注入一条）或 `"all"`（一次注入全部），构造参数与运行期 property 均可改：

```python
agent.steer(UserMessage(role="user", content="停，先回答这个"))      # 运行中插队
agent.follow_up(UserMessage(role="user", content="顺便总结一下"))     # 收尾后续跑

agent.clear_steering_queue()
agent.clear_follow_up_queue()
agent.clear_all_queues()
agent.has_queued_messages()
```

- **steering**：当前轮（含其全部工具调用）完成后、下一次 LLM 调用前注入；不跳过当前 assistant 消息已发起的工具调用。run 开始前排队的 steering 也会在首个 LLM 调用前注入。
- **follow-up**：仅当没有更多工具调用、也没有 steering 消息时检查；有则注入并再跑一轮，直到队列清空。

## 工具

继承 `AgentTool`（`nova_protocol.Tool` 的子类，pydantic 模型）定义工具：

```python
from nova_agent import AgentTool
from nova_protocol import AgentToolResult, TextContent


class SquareTool(AgentTool):
    name: str = "square"                 # LLM 可见的工具名
    description: str = "计算一个整数的平方"
    label: str = "Square"                # UI 展示名
    parameters: dict = {                 # JSON Schema
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        if on_update:
            on_update(AgentToolResult(content=[TextContent(text="计算中…")], details={}))
        result = params["x"] ** 2
        return AgentToolResult(
            content=[TextContent(text=f"{params['x']} 的平方是 {result}")],
            details={"result": result},
        )
```

`AgentToolResult` 字段：`content`（`TextContent` / `ImageContent` 列表，回给模型）、`details`（任意 JSON，供 UI 渲染或日志）、`structured_content`（匹配工具 `output_schema` 的程序化结果，不进模型上下文）、`usage`（工具自身用量，不并入主 LLM 用量）、`is_error`（默认 `False`，预期内失败置 `True`）、`terminate`（提前终止提示，见下）。实际执行的单调时钟耗时会同步写入 `ToolResultMessage.duration_ms` 与 `tool_execution_end.duration_ms`。

工具还可声明 `output_schema`（`structured_content` 的 JSON Schema）与 `replay="never" | "safe"`（结果未知时是否允许持久化宿主重放）。`after_tool_call` 只替换 `content` 时会丢弃旧 `structured_content`，避免文本与结构化结果指向不同事实。

### 参数校验与矫正

`execute` 拿到的 `params` 已经过两道处理：

1. `prepare_arguments(args)`：可选的兼容 shim，在 schema 校验前归一原始参数（如日期格式转换），返回值仍须满足 `parameters`；
2. `validate_tool_arguments()`：先按 JSON Schema 做类型矫正（`"5"` → `5`、`"true"` → `True` 等），再做 jsonschema Draft7 校验。

校验失败不会进入 `execute`，直接产出错误 toolResult 反馈给模型。`validate_tool_call(tools, tool_call)` 与 `validate_tool_arguments(tool, tool_call)` 也可独立调用。

### 执行进度

`on_update(partial_result)` 上报进度，每次调用产生一条 `tool_execution_update` 事件；可从任意线程调用（框架自动编组回事件循环）。`tool_execution_end` 保证排在所有 update 之后。update observer 异常会转成工具错误结果；run 级 listener 异常则使 run 失败并先完成子任务排水。

### 执行模式

- `tool_execution="parallel"`（默认）：同批调用按提交顺序串行完成预检（`prepare_arguments` → 校验 → `before_tool_call`），执行期经公平读写门——普通工具共享读门并发执行；声明了 `execution_mode="sequential"` 的工具取写门，等场内并发排空后独占执行。临界区为 execute + update 排干 + after（终态产生），`tool_execution_end` 与 toolResult 通知在门外。批内混有 sequential 工具**不会**把整批毒化为串行。
- `tool_execution="sequential"`：全批严格一个接一个（预检、执行、落账都完成才到下一个）。

### terminate 提前终止

工具结果、被 `before_tool_call` 拦截的结果、`after_tool_call` 的覆盖都可以带 `terminate=True`，提示跳过自动的下一轮 LLM 调用。**仅当批内全部最终结果都置了 `terminate=True`** 才生效；混合批正常继续。该提示只影响循环控制，落账的 `ToolResultMessage` 仍是标准工具结果。

### 错误处理约定

- `execute` 抛异常：框架捕获后生成错误结果（文本为异常信息，`is_error=True`）；
- 预期内失败：返回 `AgentToolResult(..., is_error=True)`；
- 工具未找到、参数校验失败、`before_tool_call` 拦截：同样产出错误 toolResult；
- assistant 消息因 token 上限被截断（`stop_reason="length"`）时，其中所有 tool call 不执行，全部标记错误（参数可能残缺，让模型下一轮重新发起）。

以上路径都不会中断 run——错误 toolResult 进入上下文，由模型在下一轮自行处理。长任务工具应定期检查 `signal.aborted` 并提前退出。

### 单次工具调用

`run_tool_call(tool_call, RunToolCallOptions(...))` 复用同一套参数校验、before/after 钩子与进度回调，但不发 Agent 事件、不写转录，适合子 Agent 或独立工具运行时。`RunToolCallOptions` 需要提供 `tools`、`assistant_message`、`context`，可选 `signal`、`on_update`、`before_tool_call`、`after_tool_call`；返回的 `AgentToolCallOutcome` 含最终 `result`、`is_error` 与 `duration_ms`，并从包根门面导出。

## 自定义消息类型

继承 `CustomAgentMessage`（住 `nova_protocol`）定义应用私有消息（UI 通知、审计记录等）：

```python
from nova_protocol import CustomAgentMessage


class NotificationMessage(CustomAgentMessage):
    role: str = "notification"
    text: str = ""
    timestamp: int = 0
```

自定义消息会进入 `state.messages` 与事件流，但 LLM 看不懂——默认 `convert_to_llm` 会过滤 system / user / assistant / toolResult 之外的消息。需要让模型感知时，自定义 `convert_to_llm` 把它们转成标准消息：

```python
def convert(messages):
    out = []
    for m in messages:
        if getattr(m, "role", None) == "notification":
            continue  # 或转成 UserMessage
        out.append(m)
    return out

agent = Agent(convert_to_llm=convert)
```

## 使用 AgentRun 消费事件

需要在中途观察事件时，使用 `prompt()` / `continue_()` 返回的 `AgentRun`：

```python
run = agent.prompt("你好")

async for event in run.events:
    print(event.type)

new_messages = await run.events.result()
await run  # 再确认完整 settle（driver、工具与状态清理全部完成）
```

`run.events` 是单消费者流。`run.events.result()` 表示收到 `agent_end`；`await run` 表示完整 settle。提前放弃时使用 `run.abort()` 和 `run.task`，不要再包一层 `asyncio.create_task()`。

内部执行函数 `run_agent_loop()` / `run_agent_loop_continue()` 仍保留给循环自身和底层契约测试；它们不是另一种公开消费模型。直接 SDK 使用者需要多个观察者时使用 `agent.subscribe()`，需要唯一系统消费者时使用 `run.events`。

## 通过 Nova Gateway 调用模型

模型服务代理不属于 Agent 内核。`nova_ai.api_impls.nova_gateway` 调用独立的 `nova_gateway_client` 官方 SDK，将公开 SSE DTO 重建为标准 `AssistantMessageEventStream`：

```python
from nova_ai import SimpleStreamOptions
from nova_ai.api_impls import nova_gateway

stream = nova_gateway.stream_simple(
    model,  # model.base_url 指向 Gateway
    context,
    SimpleStreamOptions(api_key="gateway-token"),
)
message = await stream.result()
```

下游只传 `provider/id` 和白名单选项，不传上游地址或上游凭据。AI 适配层支持文本、思考与工具参数增量重建、断连错误终态和取消；`nova_gateway` 模块可直接作为 `nova_ai.create_provider(api=...)` 的协议实现。只需要公开 DTO 时，也可直接使用不依赖 `nova_ai` 的 `nova_gateway_client`。

## 示例

[`examples/`](./examples) 目录下的五个脚本全部使用 mock `stream_fn`，离线即可运行：

| 文件 | 主题 |
|---|---|
| `01_quickstart.py` | Agent 最小用法：创建、订阅事件、prompt、状态查看 |
| `02_custom_tools.py` | 继承 `AgentTool` 实现自定义工具；JSON Schema 校验与类型矫正 |
| `03_hooks.py` | 六个 hook：工具 before/after、`on_message_end`、`prepare_request`、`prepare_next_turn`、`finish_turn` |
| `04_steering_followup.py` | `steer()` 运行中插入、`follow_up()` 停止前排队、队列 drain 模式 |
| `05_abort_continue.py` | `abort()` 取消、`wait_for_idle()` 的 shield 语义、`continue_()` 继续 |

## 开发

```bash
# 仓库根目录：统一 dev 环境并运行本包测试
pixi install --environment dev
pixi run -e dev test-agent

# 或在子包内直接运行（跳过真实 API 集成测试）
cd packages/nova_agent
pytest tests -m "not integration"

# 真实模型集成测试（env key 优先，auth.json 兜底；单探针、特性文件）
pixi run -e dev test-agent-integration
```

## License

MIT
