# Nova vs pi：扩展 API / ctx 挂载点对比

> 对比 pi（`packages/coding-agent/src/core/extensions/types.ts`）与 nova
> （`core/extensions/api.py` + `core/types/extensions/context.py`）在**扩展可及能力
> 挂在哪个对象上**的完整差异。
>
> 两家的对象模型相同：扩展工厂装载时收到 api 对象（pi 叫 `pi`，nova 叫 `nova`），
> 事件触发时 handler 收到 `(event, ctx)`。差异全在**能力挂载点的划分**。

## 分工判据（切轴不同）

| | pi | nova |
|---|---|---|
| 切轴 | **能力类型**：动作面 → api，环境面 → ctx | **生命周期**：装载期声明 → api，运行期一切 → ctx |
| 一句话规则 | 动作用 `pi.*`（闭包），环境用 `ctx.*`（参数） | 工厂里只有注册（`nova.*`），handler 里只有 `ctx.*` |
| 例外 | 有——`ctx.abort()` / `ctx.compact()` / `ctx.shutdown()` 是动作却挂在环境对象上 | 无 |

nova 判据的推论：动作（send_message/exec/set_active_tools/…）**没有活会话不成立**
（消息发给谁？注册表改谁的？abort 哪个 run？），故归代表活会话的 `ctx`；
注册不依赖会话（装载期申报"我有什么"，runner 分派前必须知道），故归 `nova`。

## 总表

| 能力 | pi | nova | 备注 |
|---|---|---|---|
| **—— 事件订阅 ——** | | | |
| `on(event, handler)` | api | api | 同位；pi 为 30+ 事件写全类型重载 |
| `on_input` | ❌（经 `on("input")`） | api | nova 多个便捷别名 |
| **—— 注册面（装载期） ——** | | | |
| `registerCommand` | api | api | |
| `registerShortcut` | api | api | |
| `registerFlag` / `getFlag` | api | api | |
| `registerProvider` / `unregisterProvider` | api | api | |
| **`registerTool`**（扩展注册 LLM 工具） | **api** | ❌ | nova 归 M4 反向工具通道（`registerDynamicTool` + `tool/invoke`） |
| **`registerMessageRenderer` / `registerEntryRenderer`** | **api** | ❌ | nova 归 M4 Node 层 `slots.register("entry:*")` |
| **spawn hook** | 无独立 API（经 `registerTool` 替换 bash 工具传 `spawnHook`） | **api（`registerSpawnHook`）** | nova 抬为 process 层一等公民：任何 spawn 类工具实现 `SpawnHookAware` 即接入 |
| `events`（扩展间总线） | api | api | |
| **—— 会话动作（运行期） ——** | | | |
| `sendMessage` / `send_message` | **api** | **ctx** | 核心分差点 |
| `sendUserMessage` / `send_user_message` | **api** | **ctx** | |
| `appendEntry` / `append_entry` | **api** | **ctx** | |
| `setSessionName` / `getSessionName` | **api** | **ctx** | |
| `setLabel` / `set_label` | **api** | **ctx** | |
| `exec`（执行 shell） | **api** | **ctx** | |
| **—— 工具/模型动作（运行期） ——** | | | |
| `getActiveTools` / `getAllTools` / `setActiveTools` | **api** | **ctx** | |
| `getCommands` / `get_commands` | **api** | **ctx** | |
| `setModel` / `set_model` | **api** | **ctx** | |
| `getThinkingLevel` / `setThinkingLevel` | **api** | **ctx** | |
| `refreshTools` / `refresh_tools` | ❌ | ctx | |
| **—— 环境/状态（运行期） ——** | | | |
| `ui`（详见下段 UI 明细） | ctx（胖） | ctx（窄） | |
| `hasUI` / `has_ui` | ctx | ctx | |
| `cwd` | ctx | ctx | |
| `mode`（tui/rpc/json/print） | ctx | ❌ | nova 已删 `ExtensionMode`，`has_ui` + `ui.capabilities` 替代 |
| `model`（当前模型） | ctx | ctx（property + `get_model()`） | |
| `sessionManager` / `session_manager` | ctx（**只读**） | ctx（含 `append_*` 可写） | nova 更宽 |
| `modelRegistry` / `model_runtime` | ctx | ctx | pi 经 registry 取 API key 直调模型；nova 经 `stream_simple` |
| `isIdle` / `is_idle` | ctx | ctx | |
| `isProjectTrusted` / `is_project_trusted` | ctx | ctx | |
| `signal` / `get_signal` | ctx | ctx | |
| `abort` | ctx ⚠️ | ctx | pi 的例外（动作挂环境对象），nova 判据下名归 ctx 名正言顺 |
| `hasPendingMessages` / `has_pending_messages` | ctx | ctx | |
| `shutdown` | ctx ⚠️ | ctx | 同上 |
| `getContextUsage` / `get_context_usage` | ctx | ctx | |
| `compact` | ctx ⚠️ | ctx | 同上 |
| `getSystemPrompt` / `get_system_prompt` | ctx | ctx | |
| `extension_path` | ❌ | ctx | |
| **—— 命令上下文增量（CommandContext） ——** | | | |
| `waitForIdle` / `wait_for_idle` | ✅ | ✅ | |
| `newSession` / `new_session` | ✅ | ✅ | |
| `fork` | ✅ | ✅ | |
| `navigateTree` / `navigate_tree` | ✅ | ✅ | |
| `switchSession` / `switch_session` | ✅ | ✅ | |
| `reload` | ✅ | ✅ | |
| `getSystemPromptOptions` / `get_system_prompt_options` | 命令 ctx | 普通 ctx 即有 | |
| `trust_project` / `untrust_project` | ❌（内置 /trust 命令） | ✅ | |
| `clone` | ❌（内置 /clone 命令） | ✅ | |
| `export` / `import_session` | ❌（内置命令） | ✅ | |
| `get_session_info` | ❌（经 sessionManager 读） | ✅ | |
| **—— UI 子对象明细（pi 胖 vs nova 窄） ——** | | | |
| `select` / `confirm` / `input` / `notify` | ✅ | ✅（反向原语，headless 安全降级：无能力即 None/False，绝不挂起） | 语义对齐 |
| 对话框 `timeout` / `signal` 选项 | ✅ | ❌ | 可后补（协议加字段） |
| `custom`（自由画布组件）/ `onTerminalInput` / `setStatus` / `setWidget` / `setFooter` / `setHeader` / `setTitle` / `editor` / `theme` 系 | ✅（同进程直驱 TUI） | ❌ | nova 归 M4 Node 层：`slots.register("region:*")` / `regions.set` / 逃生舱 |
| `setWorkingMessage` / `setWorkingIndicator` / `getToolsExpanded` 等流式 UI | ✅ | ❌ | 同上 |

## 差异要点

1. **动作面的归属是唯一主轴**：pi 把 14 个运行期动作挂在 api（handler 经闭包使用）；
   nova 全部挂 ctx（handler 单对象）。pi 的 api 因此是"注册 + 动作"双职，nova 的
   api 收窄为纯注册面。
2. **pi 自破规则的三个例外**（`ctx.abort`/`compact`/`shutdown`）在 nova 的生命周期
   判据下天然归位，无需例外条款。
3. **pi 独有挂载**：`registerTool` / `registerMessageRenderer` / `registerEntryRenderer`
   ——nova 的对位物是 M4 的反向工具通道与 slots 注册表（Node 层），非 Python 扩展面。
4. **nova 独有挂载**：`registerSpawnHook`（pi 走"替换 bash 工具"的弯路）；命令上下文的
   `trust_project` / `untrust_project` / `clone` / `export` / `import_session` /
   `get_session_info`（pi 这些是内置命令独占，扩展不可及）。
5. **UI 的胖窄**：pi 的 `ctx.ui` 同进程直驱 TUI（~25 方法）；nova 的 `ctx.ui` 只有
   反向四原语（架构 2.0：Python 运行时不驱动呈现），呈现能力归 M4 的 TS 扩展宿主。
6. **事件外动作**：pi 的 api 生命周期独立于事件，可在定时器/文件监听回调里动作
   （file-trigger 类）；nova 的 ctx 只在事件里存在——后端扩展事件驱动是刻意纪律，
   确需主动行为时锚定 `session_start` 并持有 ctx（动作是稳定委托）。
