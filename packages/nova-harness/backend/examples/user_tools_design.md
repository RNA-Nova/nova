# Nova 用户工具（Session Tool）架构设计

> 状态：**阶段 1-4 已全部落地**；bash 已按"不内置任何工具"原则外移到
> `nova_coding_agent` bundle——框架内 bash 字样清零，消息回载注册表随之外移
> 从"可延后"变为"已落地"。
> 范围：`nova_harness` core + `nova_coding_agent` bundle；对照实现为 pi（`pi/packages/coding-agent`）
> 前序讨论：bash 会话执行分析、扩展化取舍分析（本文件为最终方案）

---

## 1. 背景与问题

Nova 目前有一条"会话级 bash"链：`AgentSession.execute_bash` → `BashController` →
`LocalBashOperations`，结果记录为 `BashExecutionMessage` 注入 LLM 上下文。
它服务的是**用户/前端直接触发**的命令（`!cmd`、RPC `bash`），与模型 tool_call
触发的 LLM 工具是两条完全不同的链。

现状三个问题：

1. **机制写死成 bash 专用**：pending 缓冲、abort 级联、上下文翻译、消息记录
   全是通用机制，但都以 bash 单数形态硬编码（`_pending_bash_messages`、
   `_bash_abort_event`、`bash_execution_to_text` 静态分派）。新增同类能力
   （浏览器搜索等"主动注入上下文"的工具）必须再碰一遍 core。
2. **执行器质量弱于 pi**：截断方向错（留头不留尾）、无全量输出落盘、无滚动
   缓冲、无 ANSI/binary 清洗、abort 只杀 shell 不杀进程组。
3. **零调用方**：`modes/` 下没有任何 bash 引用，RPC 层无 `bash` 方法，
   基础设施是断头路。

## 2. 核心概念：用户工具（Session Tool）

**用户工具 = 用户/前端触发、执行结果以自定义消息类型记录并主动注入 LLM 上下文
的宿主能力。**

与 LLM 工具的分野：

| | LLM 工具（`tools/`） | 用户工具（`user_tools/`） |
|---|---|---|
| 触发者 | 模型 tool_call | 用户 / 前端 / RPC |
| 定义形态 | `executor.py` 的 `Tool` 类（元数据为类属性） | `executor.py` 的 `UserTool` 类（元数据为类属性，`__init__(session)` 注入会话） |
| 执行协议 | `AgentTool.execute` | `UserTool.execute` + abort 经 signal 级联 |
| 结果去向 | 工具消息自动进上下文 | 自定义消息类型，`to_context_text()` 翻译注入 |
| 生命周期 | 单轮内完成 | 跨 turn pending、abort 级联 |

同一领域可以两种都有（浏览器搜索既可给模型调用，也可给用户手动触发注入），
包格式上井水不犯河水。

## 3. 设计纪律（不可违反）

**泛化层只接管"管道"，不接管"能力"。**

- `UserTool` 协议的 `params` 对注册表**不透明**（各工具自声明 schema，registry 只透传）；
- `on_event` 事件通道对注册表**不透明**（各工具自定事件名，registry 只转发）；
- bash 的专属面——spawn hooks、`operations` 自定义后端（远程执行）、
  shell 命令前缀、typed Python API——**原地保留，不泛化**；
- 禁止把协议设计成最小公分母。

## 4. 总体架构：复刻 LLM 工具的四层链

LLM 工具链每层都有明确归属，用户工具平行复制：

```
LLM 工具                                        用户工具
─────────────────────────────────────         ─────────────────────────────────────
types/resources/tools.py                      types/resources/user_tools.py
  ToolDefinition                                UserToolDefinition / UserTool 协议
resources/loaders/tools.py                    resources/loaders/user_tools.py
  ToolLoader（executor.py 动态 import）          UserToolLoader（同款动态 import）
harness/tools/manager.py                      harness/user_tools/manager.py
  ToolsManager（运行时注册表）                    UserToolManager（注册表 + invoke 调度）
agent_session/controllers/tools.py            agent_session/controllers/user_tools.py
  （会话集成：激活、执行、事件）                    （pending/flush/abort 级联/消息记录）
```

现有 bash 代码的最终去向（外移落地后）：

- `controllers/bash.py` 解体：通用机制（pending、flush、abort、双写记录）
  上浮进 `controllers/user_tools.py`；bash 专属编排（前缀、spawn hook 组合）
  下沉为 bundle 用户工具的薄壳；
- 执行引擎 → `nova_coding_agent/src/nova_coding_agent/bash/engine.py`，
  LLM bash 工具（`tools/bash.py`）与会话 bash
  （`user_tools/bash.py`）在 bundle 内共享同一引擎；
- `types/extensions/process.py` 只保留扩展 spawn hook 契约
  （`BashSpawnContext`/`BashSpawnHook`/`SpawnHookAware`）——那是扩展 API
  的契约面，不是工具实现；`BashResult`/`BashOperations` 随引擎入 bundle；
- `BashExecutionMessage` → `nova_coding_agent/bash/message.py`，
  随包分发，经消息回载注册表复原（见 §10）。

## 5. 消息多态（机制泛化的前提）

`convert_to_llm` 目前对 `bashExecution` / `custom` / `branchSummary` /
`compactionSummary` 做静态分派。改为多态：

- `CustomAgentMessage`（nova_agent 层）**不改动**——它是跨包基类，不认识
  harness 的上下文概念；
- 在 harness 的 `core/types/messages.py` 定义协议：

```python
class ContextInjectable(Protocol):
    exclude_from_context: bool
    def to_context_text(self) -> str: ...
```

- `BashExecutionMessage.to_context_text()` 逐字收纳现有
  `bash_execution_to_text` 的输出格式（command/output/exit_code/取消与截断标注）；
- `convert_to_llm`：对 `bashExecution` 走 `msg.to_context_text()`；
  遇到 `ContextInjectable` 的未知自定义消息也可统一处理——这是未来包级
  用户工具的注入通道。

行为完全不变，现有测试兜底。

## 6. UserTool 协议与定义

`core/types/resources/user_tools.py`：

```python
class UserToolMessage(Protocol):
    """用户工具结果消息的契约：能翻译为 LLM 上下文文本。"""
    exclude_from_context: bool
    def to_context_text(self) -> str: ...

@dataclass
class UserToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]          # JSON Schema，给前端渲染参数表单
    execute: Callable[..., Awaitable[CustomAgentMessage]]
        # execute(params, on_event, signal) -> 消息实例（被记录+注入）
    abort: Optional[Callable[[], None]] # 无则默认用 signal 取消
    source_info: Optional[SourceInfo]
```

要点：

- `execute` 返回**消息实例**，由会话层统一记录（双写 agent state + JSONL）、
  统一 pending/flush——工具自己不碰会话状态；
- `on_event(event_name, data)` 是工具向前端推进度的通道（bash 推输出块，
  搜索推"N 条结果"），经泛型 `user_tool_event(name, event, data)` 透出 RPC；
- `signal` 贯通 abort 级联。

## 7. 运行时：Manager 与 Controller

### UserToolManager（`harness/user_tools/manager.py`）

- 注册表：`name -> UserToolDefinition`；
- **框架不内置任何用户工具**——构造时为空注册表，所有定义都来自包
  （经 AgentSession 按当前 agent 白名单注册），注册路径唯一；
- `invoke(name, params, on_event, signal)`：查表、调 execute、返回消息；
- `catalog()`：供 RPC `listUserTools` 的目录（name/description/schema）；
- 从 `PackageResolver` 接收包级 user tool 路径，经 loader 装载注册。

### UserToolController（`agent_session/controllers/user_tools.py`）

吃掉 `controllers/bash.py` 的通用机制，单数改复数：

- `_pending_session_messages: List[CustomAgentMessage]`（原 `_pending_bash_messages`）：
  流式期间工具产出的消息挂起，turn 结束 flush 到正确位置；
- **活跃取消注册表**：`Dict[str, AbortController]`（按调用 id），session abort
  时全部级联（原单个 `_bash_abort_event`）；
- 记录双写（agent state + session JSONL）逻辑原样；
- `AgentSession` 只保留泛型 API：`invoke_user_tool` / `abort_user_tool` /
  `list_user_tools`——不为具体工具提供 typed 便捷方法（外移时删除了
  `execute_bash` / `abort_bash`）。

## 8. bash 执行器质量对齐 pi（搬家时一次到位）

对照 `pi/core/bash-executor.ts` 补齐：

1. **tail 截断**：超过阈值保留**尾部**（现为头部，方向错误）；
2. **全量输出落盘**：超阈值写临时文件（`nova-bash-*.log`），
   `BashResult.full_output_path` 真正赋值（现恒为 None）；
3. **滚动缓冲**：2× 上限滚动 buffer，不再全量攒内存；
4. **ANSI/binary 清洗**：strip ANSI 转义 + 二进制输出替换 + `\r` 归一；
5. **进程组 kill**：abort 时 `killpg`（现只 `proc.kill()` 杀 shell，子进程成孤儿）。

timeout 不加——pi 的会话 bash 也没有，超时是 LLM 工具层的职责。

截断/清洗实现为 bundle `tools_common/` 下的纯函数模块（`shell.py` /
`output_accumulator.py` / `truncate.py`）——引擎外移后 harness 对它们
零生产消费，消费方本地化（对齐 pi：这些模块本就在 coding-agent 的
tools/ 层）；`child_process.py` 是进程生命周期基建（rpc/print 两个
mode 接线清场在用），留在 harness `core/utils/`。

## 9. RPC 接线

`core/rpc/protocol/methods/user_tools.py`：

- `listUserTools` → `manager.catalog()`；
- `invokeUserTool(name, params)` → controller 完整链路（执行+记录），
  进度经 `user_tool_event` 事件透出；
- `abortUserTool(name?)` → 取消注册表级联；
- **无 bash 别名**——框架不内置用户工具，RPC 面也不为具体工具提供
  别名方法（外移时删除了早期的 `bash` / `abort_bash` 别名）。

## 10. 包分发（已落地）

- `[tool.nova]` 第六类目：`user_tools = ["./user_tools/bash"]`；
- 目录形态：**仅 `executor.py`**——工具即代码，无元数据文件；
- `executor.py` 约定（`UserTool` 类）：
  - 元数据为类属性：`name` / `description` / `parameters` 必需——
    import 即可读，白名单/碰撞检测发生在会话绑定之前，无需实例；
  - `__init__(self, session)` 注入会话上下文（settings、cwd、扩展
    spawn hooks 均执行期读取）；
  - `execute(self, params, on_event, signal)` 实例方法；
  - 可选 `MESSAGE_TYPES` 类属性——加载时注册进消息回载注册表；
- 与 tools 同一纪律：**只来自已安装包**——不走顶层目录自动发现、无
  settings 直接条目、扩展不可贡献；
- agent.yaml 白名单加 `user_tools` 列表——语义与 extensions 一致：
  未声明 = 全部允许（用户工具是用户显式触发的，agent 未提及不应禁用）；
- loader / resolver / 诊断全走既有资源机制，未新造轮子。

### 消息类型回载注册表（"不内置"的入场券，已落地）

包级用户工具的消息类定义在包里，卸载后旧会话 JSONL 里的该类型消息
core 不认识。解法（`core/harness/session/message_types.py`）：

- 用户工具加载时按 `role` 注册消息类到注册表（first-wins 碰撞告警）；
- 解析层（`parse_session_entry_line`）在静态 union 校验**之前**拦截
  非静态 role 的消息条目：注册命中 → 注册类复原；注册类校验失败
  （包版本演进）→ 同样落到降级路径；
- 未命中 → 降级为 `OpaqueUserToolMessage`（`types/messages.py`）：
  原始 message dict 全量收进 `payload`、默认 `exclude_from_context=True`、
  序列化后再次解析幂等（opaque 自身静态注册，不双重包装）。
  数据不丢、上下文不炸；
- `SessionMessageEntry.message` 字段：`Union[Message, CustomMessage,
  SerializeAsAny[CustomAgentMessage]]` + dict 守卫——`SerializeAsAny`
  让子类实例按自身 schema 序列化（pydantic 默认按注解类型会丢子类
  字段）；`BeforeValidator` 守卫裸 dict 必须能验证为静态 union 成员，
  否则报错（防止空基类把畸形数据静默吞成空消息）。

外移 bash 使该注册表从"可延后"变为"必须落地"：旧会话里的
`bashExecution` 消息装了 bundle 经注册表复原，没装降级不透明。

## 11. 实施阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| 1 | 消息多态：`ContextInjectable` 协议 + `to_context_text()` + `convert_to_llm` 多态化 | ✅ |
| 2 | `UserToolDefinition`/协议/Manager；controller 泛化（pending/abort 复数化）；§8 质量对齐 | ✅ |
| 3 | RPC：`listUserTools` / `invokeUserTool` / `abortUserTool` | ✅ |
| 4 | 包分发：第六资源类目 + loader + agent.yaml 白名单 + 消息类型回载注册表 | ✅ |
| 5 | bash 外移 bundle：引擎/消息/工厂入 `nova_coding_agent`，LLM 工具与会话 bash 共享引擎；core bash 字样清零（含 RPC 别名、typed API、`_is_turn_start_message` 硬编码 role 的多态化） | ✅ |

## 12. 风险与对策

- **协议最小公分母化** → §3 纪律：params / on_event 不透明，bash 专属面不泛化；
- **消息翻译丢字段** → `to_context_text()` 逐字收纳现有格式，测试比对输出；
- **包缺席丢历史** → §10 注册表 + opaque 降级，解析层先拦截再校验；
- **union 吞数据** → `SerializeAsAny` + dict 守卫双保险（见 §10）；
- **过度工程** → registry 保持薄（查表+调度），bash executor 单文件，
  不为想象中的第三、四种工具提前抽象。
