# pi 扩展/工具边界全图（pi_boundary_map）

> 本文档完整枚举 pi（`pi/packages/coding-agent` + `pi/packages/agent`）的**全部边界面**：
> 每个注入点、拦截缝、上下文对象——谁在什么时候能碰什么、能改什么。
> 用途：作为 Nova 边界设计的对照基线。每个边界标注 Nova 现状（✅ 已有 / ⚠️ 形状不同 / ❌ 缺失）。

## 0. 边界的分层哲学

pi 的能力注入分四层，职责不混：

```
agent-core hooks（框架宿主层）  ← SDK/应用装配 Agent 时传入，扩展碰不到
工具构造层（factory options）   ← 装配期一次性注入，按工具类型化
扩展 API（运行时注册 + 事件）    ← 扩展作者的主战场
SDK 覆盖层（overrides）          ← 宿主程序的最终裁决权
```

## 1. 工具构造边界（factory options）

每个内置工具一个 options 类型，`createXTool(cwd, options)` 装配期传入：

| 工具 | options |
|---|---|
| bash | `operations`（执行后端替换）、`commandPrefix`（每条命令前缀，来自 settings.shellCommandPrefix）、`shellPath`（显式 shell，来自 settings.shellPath）、`spawnHook` |
| read | `autoResizeImages`（来自 settings.imageAutoResize）、`operations` |
| edit / write / grep / find / ls | 仅 `operations` |

- `operations` 全家族统一：执行后端可整体替换（测试/SDK 后门）。
- `spawnHook`（bash.ts:158）：`{command, cwd, env}`（env 基底为 `getShellEnv()`）→ 可改写后返回。**仅是工具构造选项，扩展 API 不可达**。
- Nova 现状：⚠️ 引擎管道已通（`create_local_bash_operations(shell_path, spawn_hook)`），executor 无参构造拿不到；settings 缺 `shell_command_prefix` 字段；spawn hook 我们反而抬到了扩展 API（见 §6.4 讨论）。

## 2. ToolDefinition 声明面（工具能声明什么）

`extensions/types.ts:438`——`registerTool` 与内置工具共用同一接口：

- 元数据：`name` / `label` / `description` / `promptSnippet` / `promptGuidelines`
- 行为：`parameters`（TypeBox）、`prepareArguments`（schema 校验前预处理）、`executionMode`（串/并行）、`renderShell`（UI 框架自绘开关）
- 执行：`execute(toolCallId, params, signal, onUpdate, ctx)`——**第 5 参 ExtensionContext**（内置 read 用 `ctx?.model` 做视觉检查，read.ts:246）
- UI：`renderCall` / `renderResult`（TUI 组件）

Nova 现状：⚠️ ToolDefinition 字段基本对齐（renderShell 无、render 回调未消费）；包工具类属性缺 `label`/`execution_mode`/`prepare_arguments`；无 ctx 通道（ToolContext 设计待落地）。

## 3. 工具注册通道与优先级

三个通道，`agent-session.ts:2429` `_refreshToolRegistry` 统一装配：

```
built-in（source 标 <builtin:name>）
  → 扩展 registerTool（标扩展 sourceInfo）
  → SDK customTools（标 <sdk:name>）     ← 同名后者覆盖前者，SDK 最高
```

- **过滤**：`allowedToolNames`（allowlist）∩ `excludedToolNames`（denylist），对三通道一视同仁（回归测试 2835 验证扩展工具也被过滤）。
- **激活**：默认新入注册表的工具自动激活；`includeAllExtensionTools` 选项；agent frontmatter 的 tools 列表另行选择。
- **addedToolNames**（wrapper.ts）：工具执行中新增激活的工具名回报给 loop，**当轮**后续 tool call 可用。
- Nova 现状：ToolsManager 枢纽同构（package → custom → override）；扩展通道明确不做（工具永远只走包管线），`addedToolNames` 当轮回报随之不做（它挂在 pi 的扩展注册通道上）。

## 4. agent-core 钩子层（扩展碰不到，宿主装配时传入）

`packages/agent/src/agent.ts` config + `types.ts`：

| 钩子 | 能力 |
|---|---|
| `convertToLlm` | 消息 → LLM 上下文转换全替换 |
| `transformContext` | LLM 调用前改写消息数组 |
| `beforeToolCall` | 阻断工具执行（`block` + `reason`）；能看到 assistantMessage/toolCall/已校验 args/context |
| `afterToolCall` | 字段级覆盖结果：`content`/`details`/`isError`/`terminate`（整批 terminate 才提前终止） |
| `prepareNextTurn(WithContext)` | 下一轮是否继续/如何继续 |
| `shouldStopAfterTurn` | 本轮后是否停止 |

Nova 现状：✅ convert_to_llm（多态）、context 事件（≈transformContext 但开放给扩展）、tool_call/tool_result 事件（≈before/afterToolCall，开放给扩展且 tool_call 支持原地改 input——pi 的 beforeToolCall 不能改 args，只能 block）；prepare_next_turn/should_stop_after_turn 我们是**扩展事件**（pi 是宿主钩子）——Nova 在这层全面更开放。

## 5. 扩展事件拦截缝（on() 全集）

`extensions/types.ts:1171`——每个事件能改什么：

| 事件 | 能做什么 |
|---|---|
| `input` | transform（改文本/图片，链式）/ handled（短路）/ continue |
| `before_agent_start` | 注入 CustomMessage + 链式替换 systemPrompt |
| `context` | 链式改写 messages（deep clone 后传入） |
| `before_provider_request` | 链式替换整个 payload |
| `before_provider_headers` | **原地改 headers**（null 删除该头） |
| `after_provider_response` | 观察响应 status/headers（流消费前） |
| `message_end` | 链式替换消息（role 必须不变） |
| `tool_call` | block + reason；**原地改 event.input**（不重校验，后续 handler 可见） |
| `tool_result` | 链式改 content/details/isError |
| `user_bash` | 返回 `operations`（替换执行后端）或 `result`（完全接管执行） |
| `session_before_switch/fork/compact/tree` | cancel 否决（+ fork 跳过恢复、compact 自带结果、tree 覆盖总结参数） |
| `project_trust` | yes/no/undecided 裁决 + remember |
| `resources_discover` | 贡献 skill/prompt/theme 路径 |
| 纯通知 | session_start/info_changed/compact/tree/shutdown、agent_start/end/**agent_settled**、turn_start/end、message_start/update、tool_execution_start/update/end、model_select、thinking_level_select |

Nova 现状：✅ 大部分对齐（含 pi 没有的 prepare_next_turn/should_stop_after_turn 扩展事件、registerSpawnHook）；❌ 缺 `agent_settled`、`before_provider_headers` 两个事件。

## 6. 扩展注册面（registerXxx 全集）

`loader.ts:228` 起：

| 方法 | 注册什么 | Nova 现状 |
|---|---|---|
| `on(event, handler)` | 事件订阅（29 种） | ✅ |
| `registerTool(def)` | LLM 工具（写 extension.tools + refreshTools） | ❌（明确不做：工具只走包管线） |
| `registerCommand(name, opts)` | slash 命令（含参数补全） | ✅（含 :N 冲突改名） |
| `registerShortcut(key, opts)` | 快捷键（与内置/用户冲突检测） | ✅ |
| `registerFlag(name, opts)` | CLI flag（默认值入 runtime.flagValues） | ✅ |
| `registerMessageRenderer(type, fn)` | CustomMessage 渲染 | ✅（未消费，UI 层债） |
| `registerEntryRenderer(type, fn)` | CustomEntry 渲染 | ❌（UI 层债） |
| `registerProvider/unregisterProvider` | 模型 provider（load 期排队，bind 后即时） | ✅ |

注意：**pi 扩展 API 没有 spawn hook**——那是 bash 工具构造选项。Nova 曾自发明 registerBashSpawnHook（pi 无对应物），已抬抽象为 process 层的 registerSpawnHook（types/extensions/process.py）。

## 7. 扩展上下文对象（扩展能摸到多少世界）

| 上下文 | 场景 | 内容 |
|---|---|---|
| `ExtensionContext` | 事件 handler | ui（30+ 方法）、mode/hasUI/cwd、sessionManager（只读）、modelRegistry、**model**（当前模型 live）、isIdle/isProjectTrusted、signal/abort、hasPendingMessages、shutdown、getContextUsage、compact、getSystemPrompt |
| `ExtensionCommandContext` | 命令 handler | 以上 + getSystemPromptOptions、waitForIdle、newSession/fork/navigateTree/switchSession（均带 withSession 回调拿新 ctx）、reload |
| `ReplacedSessionContext` | 会话替换后 | 命令上下文 + sendMessage/sendUserMessage |
| `ProjectTrustContext` | trust 裁决 | cwd/mode/hasUI + ui 四方法子集 |

stale 防护：每个属性 getter 内 assertActive()，会话替换/reload 后旧 ctx 任何读取即抛错。
Nova 现状：⚠️ 形状对齐但是急切快照（无逐属性 stale 检查）；UIContext 为能力发现模型（重设计清单在案）。

## 8. 扩展 actions（扩展主动做什么）

sendMessage（可 triggerTurn/deliverAs）、sendUserMessage、appendEntry、setSessionName/getSessionName、setLabel、exec（执行 shell）、getActiveTools/getAllTools/setActiveTools、getCommands、setModel、getThinkingLevel/setThinkingLevel、`events`（扩展间总线）。
Nova 现状：✅ 全对齐。

## 9. SDK 覆盖层（宿主最终裁决权）

- `baseToolsOverride`：整体替换内置工具集；
- `customTools`：SDK 直接塞 ToolDefinition（最高优先级）；
- ResourceLoader overrides（构造参，`resource-loader.ts:139`）：`extensionsOverride` / `skillsOverride` / `promptsOverride` / `themesOverride` / `agentsFilesOverride` / `systemPromptOverride` / `appendSystemPromptOverride`——宿主程序包裹任意资源类的加载结果。

Nova 现状：✅ base_tools_override / custom_tools 有；❌ 资源加载 override 回调缺（systemPrompt 归 agent 体系可豁免，contextFiles/agentsFiles 类可后补）。

## 10. 边界设计的三条规律（pi 的隐含原则）

1. **谁装配谁定制**：内置工具由 agent-session 装配，所以 options 按工具类型化（框架认识自己的工具）；扩展/SDK 工具走统一 ToolDefinition（框架不认识它们）。
2. **拦截缝开在"值流过的地方"**：payload、headers、messages、input、tool args/result——扩展改的是数据，不是流程；流程否决只有 cancel/block/handled 三个原语。
3. **上下文按场景分级**：事件 ctx < 命令 ctx < 替换后 ctx，能力逐级放大；stale ctx 逐属性抛错。

## 附：Nova 缺口速查（据本图）

| 缺口 | 严重度 | 状态 |
|---|---|---|
| ToolContext（§1 构造供养） | — | ✅ 已落地（构造期 ToolContext + 执行期 ToolExecContext 第 5 参，对齐 pi ctxFactory） |
| `agent_settled` / `before_provider_headers` 事件（§5） | 低 | 待接线 |
| registerTool（§3 扩展工具通道） | — | 明确不做（工具只走包管线） |
| addedToolNames 当轮回报（§3） | — | 不做（挂在 pi 扩展注册通道上） |
| 资源加载 override 回调（§9） | 低 | 后补 |
| registerEntryRenderer（§6） | UI 层 | 随 UI 重设计 |
| ctx stale 逐属性防护（§7） | 中 | 待对齐 |
