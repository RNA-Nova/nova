# nova_harness.core 与 TS `coding-agent/src/core` 差异评估报告

> 生成时间：2026-06-10
> 对比目录：
> - Python: `/root/nova/packages/nova_harness/src/nova_harness/core`
> - TypeScript: `/root/pi/packages/coding-agent/src/core`

## 分类约定

- ✅ **已对齐**：功能/语义基本一致，仅命名或语言习惯差异。
- ⚠️ **已实现但有偏差**：功能存在，但行为、事件 reason、数据模型或返回值与 TS 不完全一致。
- ❌ **Python 缺失**：TS 有明确实现，Python 没有或仅占位。
- ➕ **Python 多出**：Python 有而 TS 没有的实现或组织方式。

---

## 一、AgentSessionRuntime

**TS**: `agent-session-runtime.ts`  
**Python**: `agent_session/runtime.py`

### ✅ 已对齐

- `newSession` / `switchSession` / `fork` / `importFromJsonl` / `dispose` 五个生命周期方法骨架一致。
- `fork()` 逻辑已验证与 TS 一致，测试通过（`test_runtime_lifecycle.py` 16 passed）。
- `setRebindSession` / `setBeforeSessionInvalidate` 钩子都有对应实现。
- `SessionImportFileNotFoundError` 都有对应异常。
- `_finishSessionReplacement` 的 `withSession` 回调现在传入 `session.createReplacedSessionContext()` 返回的 enriched command context（与 TS 对齐）。
- `assertSessionCwdExists()` / `MissingSessionCwdError` 已通过对等模块 `core/session_cwd.py` 实现，并在 `switch_session`、`import_from_jsonl`、`create_agent_session_runtime` 入口处调用。`getMissingSessionCwdIssue` 的 `session_file` 前置检查已修复：当 `sessionManager.getSessionFile()` 为空时返回 `undefined/None`，避免内存会话在 cwd 缺失时误报。
- `SessionStartEvent` / `SessionShutdownEvent` 的 `reason` 类型已与 TS 对齐：`"startup" | "reload" | "new" | "resume" | "fork"` 与 `"quit" | "reload" | "new" | "resume" | "fork"`。
- `switchSession` 的 `session_start.reason` 统一为 `"resume"`。
- `dispose()` 的 `session_shutdown.reason` 统一为 `"quit"`。
- `_teardownCurrent` / `dispose()` 的 `session_shutdown` 事件发射已改为 TS 的 `emitSessionShutdownEvent` 语义：仅当扩展注册了 `session_shutdown` handler 时才 emit。
- `bindExtensions` 调用位置已对齐：Python runtime 的 `newSession` / `switchSession` / `fork` 不再内部调用 `bind_extensions()`；改由调用方（如 RPC server）通过 `set_rebind_session` 回调在 session 替换后调用。
- `switchSession` 支持 `projectTrustContextFactory` 参数，为切换后的 cwd 重新生成 project trust context。
- `CreateAgentSessionRuntimeResult.modelFallbackMessage` 已由 `_resolve_initial_model()` 生成并传递。

### ⚠️ 已实现但有偏差

| TS | Python | 影响 | 建议 |
|---|---|---|---|
| `_apply()` 只替换 session/services/diagnostics/modelFallbackMessage | Python 额外调用 `self._session.bind_runtime(self)` | Python 让 session 反向持有 runtime，TS 不这样；属于扩展行为，不影响功能对齐 | 可保留 |
| `_emitBeforeSwitch` 直接访问 `this.session.extensionRunner.hasHandlers(...)` | Python 检查 runner 为 None 时安全返回 `{cancelled: False}` | runner 未初始化时 TS 会抛 `TypeError`，Python 更防御性；不影响正常流程 | 可保留 |
| `createAgentSessionRuntime(createRuntimeFactory, options)` 接收工厂 | Python `create_agent_session_runtime(options)` 是高层便利函数，内部创建服务与工厂 | 接口不同，但功能等价 | 如要严格对齐，可拆分出接收工厂的底层函数 |
| `importFromJsonl` 后调用 `finishSessionReplacement()` 不传递 `withSession` | Python 相同 | 一致 | - |

### ❌ Python 缺失

无。

### ➕ Python 多出

- `reload()`：重新加载资源、系统提示词并重新绑定扩展。TS runtime 没有直接等价方法。

---

## 二、AgentSession

**TS**: `agent-session.ts`  
**Python**: `agent_session/agent.py`

### ✅ 已对齐

- prompt / continue / clearQueue / waitForIdle / hasQueuedMessages
- bash 执行、retry、compaction、tree navigation
- 模型/thinking/工具切换
- 扩展绑定、事件订阅
- `sendUserMessage` / `sendCustomMessage` 底层都有实现
- `createReplacedSessionContext()`：返回 enriched `ExtensionCommandContext`，额外提供 `sendMessage` / `sendUserMessage`
- `exportToJsonl(outputPath?)`：导出当前分支到 JSONL
- `getLastAssistantText()`：获取最后一条 assistant 文本
- `hasExtensionHandlers(eventType)`：检查扩展是否注册了某事件处理器
- `_baseSystemPromptOptions` 缓存：`SystemPromptManager` 提供 `build_system_prompt_options()`，`AgentSession` 缓存 `_base_system_prompt_options`，`ExtensionRunner.get_system_prompt_options()` 返回完整选项

### ❌ Python 缺失

| 能力 | 说明 | 优先级 |
|---|---|---|
| `exportToHtml(outputPath?)` | 导出当前会话到独立 HTML，含主题、模板、marked/highlight、工具渲染 | 中（工作量大） |

---

## 三、SessionManager

**TS**: `session-manager.ts`  
**Python**: `harness/session/manager.py`

### ✅ 已对齐

- JSONL 持久化、header + entries 结构
- `buildSessionContext` 树遍历、compaction/branch summary 处理
- `createBranchedSession` / `newSession` / `open` / `create` / `inMemory` 工厂
- 标签、session_info、custom/custom_message entries

### ⚠️ 已实现但有偏差

| TS | Python | 说明 |
|---|---|---|
| leaf 指针 = 最后一个 entry 的 id | Python 使用独立的 `LeafEntry` 类型持久化 leaf 变更 | 数据模型不兼容。TS 读 Python 生成的带 `leaf` entry 的 JSONL 会把它当成普通 entry；Python 读 TS 会话能工作，因为 `_build_index` 会把最后一个 entry 当 leaf。 |
| entries 类型里没有 `active_tools_change` | Python 有 `ActiveToolsChangeEntry` | Python 多出一种 entry 类型，TS 不会识别。 |
| `_persist` 使用 `openSync(..., "wx")` 原子创建 + 批量写入 | Python 使用普通 `open(..., "w")` | 行为基本一致，并发/原子性有差异。 |
| `usesDefaultSessionDir()` | Python 没有 | 低影响 |

### ➕ Python 多出

- `SessionManager.fork_from(source_path, target_cwd)`：TS 没有直接等价物。
- `set_leaf_id` / `branch` / `reset_leaf` / `branch_with_summary`：TS 中 leaf 变更通过 `AgentSession.navigateTree` 写 entries，Python 单独抽出这些方法。

---

## 四、扩展系统

**TS**: `extensions/`  
**Python**: `agent_session/extensions/`

### ✅ 已对齐

- ExtensionAPI / ExtensionContext / ExtensionCommandContext 三层上下文
- `ExtensionRunner.bind_core(actions, contextActions, providerActions?)` action-injection 模型
- `ExtensionRunner.bind_command_context(actions?)` 命令上下文 action 注入
- `ExtensionRuntime` 仅持有 `ExtensionActions`；`compact` / `getSystemPrompt` 通过 `ExtensionContextActions` 注入 runner，与 TS 一致
- `NovaExtensionAPI` 不再暴露 `compact` / `get_system_prompt`，和 TS `ExtensionAPI` 一致
- `sendMessage` 改为接收 `{customType, content, display, details}` 消息对象（已修复原 `text: str` 签名 bug）
- `compact` 改为接收 `CompactOptions` 对象（已修复原 `custom_instructions: str` 签名 bug）
- `unregister_provider` 改为走 `runtime.unregister_provider`，和 TS 一致
- `ExtensionContext` / `ExtensionCommandContext` 每次访问属性/方法都校验 runner 是否有效（stale 保护）
- `NovaExtensionAPI.get_flag()` 语义与 TS `getFlag` 对齐：返回当前运行时值，未设置则回退默认值
- `emit_before_agent_start` 将 `ctx.get_system_prompt()` 重写为当前正在构建的 system prompt
- 命令解析保留原 `name` 并新增 `invocation_name`，`get_command()` 按 `invocation_name` 匹配
- `get_commands` action 返回 `SlashCommandInfo[]`（已引入 `SlashCommandInfo` 类型，含 `source`/`source_info`，与 TS 对齐）
- `get_all_tools` action 返回 `ToolInfo[]`（已引入/复用 `ToolInfo` 类型，剔除执行体与渲染回调）
- `set_model` 返回 `bool`（缺少 API key 时返回 `False`，与 TS `setModel` 对齐）
- `ExtensionAPI.exec(command, args, options?)` 已实现，返回 `ExecResult`（对应 TS `ExecOptions`/`ExecResult`）
- `get_shortcuts(resolved_keybindings)` 已与内置 keybindings 做冲突检测：reserved 内置快捷键跳过并告警，非 reserved 内置快捷键允许扩展覆盖并记录诊断
- `NovaExtensionAPI.register_command` / `register_shortcut` / `register_flag` 同时支持 TS 风格的 `(name/key, options)` 调用与原有的 dataclass 对象调用
- 事件分发、工具包装、provider 注册排队/刷新
- `session_before_switch` / `session_before_fork` / `session_shutdown` / `session_start`
- `sendUserMessage` / `setModel` / `setActiveTools` 等 actions
- `getSystemPromptOptions()` 返回完整选项
- `ReplacedSessionContext` 等价机制通过 `AgentSession.create_replaced_session_context()` 提供

### ❌ 仍存在的差异

| TS | Python | 说明 | 优先级 |
|---|---|---|---|
| `Extension`  collections 用 `Map` | Python `Extension` 用 `list` / `dict` | 内部表示不同，不影响外部语义 | 低 |
| TS `eventBus` 独立于 runner，invalidate 不清除 | Python runner 持有 `event_bus`，`invalidate()` 会 `clear()` | 生命周期管理差异 | 低 |
| Python 多出 `prepare_next_turn` / `should_stop_after_turn` 事件 | TS 无对应事件 | Python 额外能力，非缺失 | 低 |
| TS `ExtensionUIContext` 为同步接口 | Python `UIContext` 为异步 capability-based 抽象 | 前端架构差异，不影响扩展 API 基本使用 | 低 |

---

## 五、导出 / 工具 / 杂项

### ❌ Python 缺失（TS 有独立文件/目录）

| TS 文件/目录 | Python 状态 | 说明 |
|---|---|---|
| `export-html/` | 完全没有 | 包含 HTML/CSS/JS 模板、marked/highlight vendor、ANSI→HTML、工具预渲染。移植工作量大。 |
| `tools/` 目录 | Python core 没有等价目录 | Python 的内置工具可能分散在 `core/utils/bash.py`、其他包或尚未实现。需确认 `read/write/edit/grep/find/ls` 工具位置。 |
| `session-cwd.ts` | 已实现 | 对应 `core/session_cwd.py`，提供 `assert_session_cwd_exists()` 与 `MissingSessionCwdError` |
| `auth-guidance.ts` | 完全没有 | 用户鉴权失败时的友好提示 |
| `model-resolver.ts` | 完全没有 | CLI 模型模式解析 |
| `provider-attribution.ts` | 完全没有 | 请求头合并 |
| `provider-display-names.ts` | 完全没有 | 提供商显示名 |
| `slash-commands.ts` | 基本对齐 | Python 已引入 `SlashCommandInfo` / `SlashCommandSource` 类型，`get_commands` 返回结构与 TS 一致；暂未维护独立的内置 slash command 列表（当前无内置 slash 命令） |
| `event-bus.ts` | 不完全 | Python 有 `ExtensionEventBus`，但用途可能不同 |
| `telemetry.ts` / `timings.ts` / `experimental.ts` | 完全没有 | 遥测、性能计时、实验特性开关 |
| `keybindings.ts` / `footer-data-provider.ts` / `output-guard.ts` | 不在 core 中 | Python 的 `modes/rpc/output_guard.py` 覆盖了 `output-guard`；其余可能在 `modes/` 或外部 TUI（Node 前端）中 |

### ➕ Python 多出的模块/组织

| Python | TS 状态 | 说明 |
|---|---|---|
| `agent_session/controllers/` | TS 没有 | 把 AgentSession 的各领域逻辑拆成 controller |
| `core/package/` 子包 | TS 只有 `package-manager.ts` | Python 拆得更细 |
| `core/types/` 集中数据模型 | TS 类型分散在各模块 | 组织方式差异 |
| `core/config/storage/backends.py` | TS 没有独立 storage backend | Python 抽象了存储后端 |

---

## 六、关键差距优先级（更新后）

| 优先级 | 差距项 | 实现成本 | 说明 |
|---|---|---|---|
| 低（工作量大） | `exportToHtml` | 高 | 需移植模板、主题、ANSI→HTML、工具渲染。 |
| 低 | 数据模型差异：`LeafEntry` / `ActiveToolsChangeEntry` | 中 | 当前内部自洽，但与 TS 互读会话文件可能出问题。 |

---

## 七、建议实施顺序（更新后）

1. **第一批（大功能）**
   - `exportToHtml` 作为独立 feature 任务。
