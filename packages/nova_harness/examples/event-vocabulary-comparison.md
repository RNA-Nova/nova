# 三家事件体系与词汇设计穷尽对比（pi / codex / nova）

> 本文档穷尽列出 pi、codex、nova 三个系统的事件类型、交互词汇与实体模型，
> 所有条目附源码位置（行号为 2026-08 工作区状态）。
> 用途：nova 事件/词汇体系演化的对照基准（"custom 通道包级 item 化"设计的参照系）。

---

## 0. 一张总表

| 维度 | pi | codex | nova |
|---|---|---|---|
| 进程模型 | 单体进程内 | Rust 多 crate，app-server 多客户端 | Python 后端 + Node/浏览器前端（JSON-RPC stdio/WS） |
| 核心事件 | `AgentEvent` 10 型 | `EventMsg` 81 变体 | `AgentEvent` 10 型 |
| 会话层事件 | `AgentSessionEvent` 11 型 | —（thread 层即会话） | harness 自产 16 型（合 Bus 2 共 26 型） |
| 线上事件 | （无线上） | `ServerNotification` 72 个 | `agent/event` 信封 26 型 + `seq/ts/sessionId` 锚点 |
| 事件翻译层 | 无（直达 UI） | 服务器侧（bespoke → item） | 客户端侧（mirror mapping 归约） |
| 实体模型 | 消息 + custom 消息 | **ThreadItem 18 变体**（user/LLM 统一，`source` 分源） | 消息 + custom 消息 + toolCall 卡片 |
| 正向方法 | （进程内 API） | `ClientRequest` **137 个** | RPC 方法 **76 个**（8 域） |
| 反向请求 | （进程内 `ctx.ui` 27 件） | `ServerRequest` **11 个**（9 新 + 2 废弃） | `ui/request` 泛型 1 帧（自由 method） |
| 反向撤销/通知 | （race） | 无撤销帧；通知即 ServerNotification | `ui/cancel` + `ui/notify` 两帧 |
| 能力协商 | 无 | experimental opt-in（无降级） | `system/capabilities` + `has_capability` 降级 |

---

## 1. codex（Rust，app-server 体系）

### 1.1 core `EventMsg`（81 变体）

`protocol/src/protocol.rs:1287-1497`（`#[serde(tag="type")]`，`TurnStarted/TurnComplete` 线上名 `task_started/task_complete`）。

**错误/警告（5）**：Error、Warning、GuardianWarning、DeprecationNotice、StreamError
**realtime（6）**：RealtimeConversationStarted/Realtime/Closed/Sdp、RealtimeConversationListVoicesResponse、（+实时类）
**模型（3）**：ModelReroute、ModelVerification、SafetyBuffering
**会话/线程（9）**：SessionConfigured、ContextCompacted、ThreadRolledBack、ThreadSettingsApplied、ThreadGoalUpdated、ThreadQueueChanged、EnvironmentConnected、EnvironmentDisconnected、ShutdownComplete
**turn（6）**：TurnStarted、TurnComplete、TurnAborted、TurnDiff、TurnModerationMetadata、TokenCount
**消息/推理（12）**：AgentMessage、UserMessage、AgentReasoning、AgentReasoningRawContent、AgentReasoningSectionBreak、AgentMessageContentDelta、PlanUpdate、PlanDelta、ReasoningContentDelta、ReasoningRawContentDelta、RawResponseItem、RawResponseCompleted
**工具/执行（17）**：McpStartupUpdate、McpStartupComplete、McpToolCallBegin、McpToolCallEnd、WebSearchBegin、WebSearchEnd、ImageGenerationBegin、ImageGenerationEnd、ExecCommandBegin、ExecCommandOutputDelta、TerminalInteraction、ExecCommandEnd、ViewImageToolCall、PatchApplyBegin、PatchApplyUpdated、PatchApplyEnd、（+进程类）
**审批/请求（8）**：ExecApprovalRequest、RequestPermissions、RequestUserInput、DynamicToolCallRequest、DynamicToolCallResponse、ElicitationRequest、ApplyPatchApprovalRequest、GuardianAssessment
**item/钩子（6）**：ItemStarted、ItemCompleted、HookStarted、HookCompleted、EnteredReviewMode、ExitedReviewMode
**多智能体（9）**：CollabAgentSpawnBegin/End、CollabAgentInteractionBegin/End、CollabWaitingBegin/End、CollabCloseBegin/End、CollabResumeBegin/End、SubAgentActivity

### 1.2 `ThreadItem`（18 变体——实体模型核心）

`app-server-protocol/src/protocol/v2/item.rs:226-397`（`#[serde(tag="type")]`，均含 `id`）：

| # | 变体 | 语义 / 关键字段 |
|---|---|---|
| 1 | `UserMessage` | 用户消息（`content: Vec<UserInput>`，`client_id?`） |
| 2 | `HookPrompt` | hook 注入提示（`fragments`，含 `hook_run_id`） |
| 3 | `AgentMessage` | 助手消息（`text`、`phase?`、`memory_citation?`） |
| 4 | `Plan` | 计划项（experimental，`text`） |
| 5 | `Reasoning` | 推理（`summary`/`content`） |
| 6 | `CommandExecution` | 命令执行（`command`/`cwd`/`source`/`status`/`command_actions`/`aggregated_output?`/`exit_code?`/`duration_ms?`/`process_id?`） |
| 7 | `FileChange` | 文件补丁（`changes: Vec<FileUpdateChange>`，`status: PatchApplyStatus`） |
| 8 | `McpToolCall` | MCP 工具调用（`server`/`tool`/`status`/`arguments`/`result?`/`app_context?`） |
| 9 | `DynamicToolCall` | 客户端侧动态工具调用（`tool`/`arguments`/`status`） |
| 10 | `CollabAgentToolCall` | 多智能体协作调用（spawnAgent/sendInput/resume/wait/close） |
| 11 | `SubAgentActivity` | 路径制子代理活动（`agent_thread_id`/`agent_path`） |
| 12 | `WebSearch` | 网络搜索（`query`/`action?`/`results?`） |
| 13 | `ImageView` | view_image 工具查看本地图（`path`） |
| 14 | `Sleep` | sleep 扩展项（`duration_ms`） |
| 15 | `ImageGeneration` | 图像生成（`status`/`result`/`saved_path?`） |
| 16 | `EnteredReviewMode` | 进入评审模式 |
| 17 | `ExitedReviewMode` | 退出评审模式 |
| 18 | `ContextCompaction` | 上下文压缩标记（仅 `id`） |

枚举：`CommandExecutionStatus = inProgress/completed/failed/declined`（item.rs:997）；
**`CommandExecutionSource = agent / userShell / unifiedExecStartup / unifiedExecInteraction`**（item.rs:1031——用户的 `!cmd` 与 LLM 的 shell 调用**同类型**，source 分源）。

### 1.3 `ServerNotification`（72 个——线上事件）

`common.rs:1692-1795`（宏 `server_notification_definitions!`）。

- **全局（5）**：`error`、`warning`、`guardianWarning`、`deprecationNotice`、`configWarning`
- **thread（14）**：`thread/started`、`status/changed`、`archived`、`deleted`、`unarchived`、`closed`、`name/updated`、`goal/updated`、`goal/cleared`、★`settings/updated`、`tokenUsage/updated`、◆`compacted`、★`environment/connected|disconnected`
- **turn（6+2 hook）**：`turn/started`、`completed`、`diff/updated`、`plan/updated`、★`moderationMetadata`、`hook/started|completed`
- **item（19）**：`item/started`、`completed`、`autoApprovalReview/started|completed`、`agentMessage/delta`、★`plan/delta`、`commandExecution/outputDelta`、`commandExecution/terminalInteraction`、◆`fileChange/outputDelta`、`fileChange/patchUpdated`、`mcpToolCall/progress`、`reasoning/summaryTextDelta`、`reasoning/summaryPartAdded`、`reasoning/textDelta`、◆`rawResponseItem/completed`、◆`rawResponse/completed`
- **进程（3）**：`command/exec/outputDelta`、★`process/outputDelta`、★`process/exited`
- **server 请求回执（1）**：`serverRequest/resolved`
- **MCP（2）**：`mcpServer/oauthLogin/completed`、`startupStatus/updated`
- **account（3）**：`account/updated`、`rateLimits/updated`、`login/completed`
- **realtime（8，全 experimental）**：`thread/realtime/started|itemAdded|transcript/delta|transcript/done|outputAudio/delta|sdp|error|closed`
- **生态/其他（13）**：`app/list/updated`、`externalAgentConfig/import/progress|completed`、`skills/changed`、`fs/changed`、`model/rerouted|verification|safetyBuffering/updated`、`remoteControl/status/changed`、`fuzzyFileSearch/sessionUpdated|completed`、`windows/worldWritableWarning`、`windowsSandbox/setupCompleted`
- 封套 `ServerNotificationEnvelope` = 通知本体 + `emittedAtMs`（**无全局序号**）

### 1.4 `ServerRequest`（11 个——反向请求）

`common.rs:1537-1608`。**新 9 + 废弃 2**：

| 线上方法名 | 语义（应答者） |
|---|---|
| `item/commandExecution/requestApproval` | 命令执行审批（**人**；params 含 `command`/`cwd`/`reason`/`command_actions`/`additional_permissions?`） |
| `item/fileChange/requestApproval` | 文件改动审批（**人**） |
| `item/permissions/requestApproval` | 追加权限审批（**人**） |
| `item/tool/requestUserInput` | 工具向用户提问（**人**，experimental；`questions[]`/`is_blocking`/`auto_resolution_ms?`） |
| `mcpServer/elicitation/request` | MCP 服务器经客户端提问（**人**） |
| `item/tool/call` | **客户端执行动态工具调用**（机器；反向工具调用） |
| `account/chatgptAuthTokens/refresh` | 客户端刷新 token（机器） |
| `attestation/generate` | 生成 attestation token（机器） |
| `currentTime/read` | 读客户端外部时钟（机器，experimental） |
| ~~`applyPatchApproval`~~（废弃） | v1 遗留审批 |
| ~~`execCommandApproval`~~（废弃） | v1 遗留审批 |

### 1.5 `ClientRequest`（137 个——正向方法）

`common.rs:474-1267`（`client_request_definitions!`）。按域：

- 握手/诊断（2）：`initialize`、★`server/diagnostics`
- thread 生命周期（18）：`thread/start|resume|fork|archive|delete|unarchive|unsubscribe|rollback|read|list|loaded/list|compact/start|shellCommand|inject_items|metadata/update|section/move|approveGuardianDeniedAction|name/set`
- thread elicitation/goal/settings（8）、后台终端（3★）、搜索/历史（4★）、threadSection（4）
- turn（3）：`turn/start|steer|interrupt`；review（1）；realtime（6★）
- skills/hooks（4）；marketplace（3）；plugin（12）；app（3）
- fs（9）：`fs/readFile|writeFile|createDirectory|getMetadata|readDirectory|remove|copy|watch|unwatch`
- model/特性（5）；remoteControl（7★）；collaborationMode（1★）；mock（1★）
- environment（3★）；mcp（5）；windowsSandbox（2）；account（9）；feedback（1）
- command/exec（4）：`command/exec|write|terminate|resize`；process（4★）；config（4）；externalAgentConfig（4）
- DEPRECATED v1（7）

> 每条带 `serialization:` 并发键（`thread_id`/`global`/None——codex 的 per-key 串行化队列）。

---

## 2. pi（TypeScript，单体进程内）

### 2.1 `AgentEvent`（10 型——agent 循环事件）

`packages/agent/src/types.ts:415-430`（nova_agent 的 AgentEvent 与其逐项对位）：

| 事件 | 语义 | 关键字段 |
|---|---|---|
| `agent_start` | 一次运行开始 | — |
| `agent_end` | 运行结束（监听者 settle 后才 idle） | `messages` |
| `turn_start` | 一个 turn 开始 | — |
| `turn_end` | 一个 turn 完成 | `message`、`toolResults` |
| `message_start` | 消息开始（user/assistant/toolResult 均发） | `message` |
| `message_update` | assistant 流式增量 | `message`、`assistantMessageEvent` |
| `message_end` | 消息完成 | `message` |
| `tool_execution_start` | 工具开始执行 | `toolCallId`、`toolName`、`args` |
| `tool_execution_update` | 执行中流式部分结果 | + `partialResult` |
| `tool_execution_end` | 工具执行结束 | + `result`、`isError` |

### 2.2 `AgentSessionEvent`（11 型——session 层自产）

`packages/coding-agent/src/core/agent-session.ts:136-162`：

`agent_end`（替换版，带 `willRetry`）、`agent_settled`、`queue_update`（steering/followUp 队列）、`compaction_start`（reason: manual/threshold/overflow）、`compaction_end`、`entry_appended`（`entry: SessionEntry`）、`session_info_changed`（name）、`thinking_level_changed`、`auto_retry_start`、`auto_retry_end`

### 2.3 扩展事件（33 个——`pi.on()` 可订阅）

`packages/coding-agent/src/core/extensions/types.ts:1019-1044`：

- **启动/资源（2）**：`project_trust`（信任裁决）、`resources_discover`
- **会话（9）**：`session_start`（reason: startup|reload|new|resume|fork）、`session_info_changed`、`session_before_switch`（可取消）、`session_before_fork`（可取消）、`session_before_compact`、`session_compact`、`session_shutdown`、`session_before_tree`、`session_tree`
- **Agent/请求（16）**：`context`（改消息）、`before_provider_request`（换 payload）、`before_provider_headers`（改头）、`after_provider_response`、`before_agent_start`（注入消息/换系统提示词）、`agent_start`、`agent_end`、`agent_settled`、`turn_start`、`turn_end`、`message_start`、`message_update`、`message_end`（可替换）、`tool_execution_start|update|end`
- **模型（2）**：`model_select`、`thinking_level_select`
- **用户/输入（2）**：`user_bash`（`!`/`!!` 拦截——可接管执行/换 operations）、`input`（可 transform/handled）
- **工具（2）**：`tool_call`（执行前可 block/改 input）、`tool_result`（执行后可改 content/details/isError）

### 2.4 `ctx.ui`（27 件——进程内 UI 原语）

`extensions/types.ts:127-278`（TUI 实现在 interactive-mode.ts:2114-2166；非交互场景只暴露 select/confirm/input/notify 四件）：

- **问用户（4）**：`select(title, options, opts?)`、`confirm(title, message, opts?)`、`input(title, placeholder?, opts?)`、`editor(title, prefill?)`（多行编辑器）
- **通知/状态（6）**：`notify(message, type)`、`setStatus(key, text)`、`setWorkingMessage`、`setWorkingVisible`、`setWorkingIndicator`、`setHiddenThinkingLabel`
- **区域/整件（4）**：`setWidget(key, content, options?)`、`setFooter(factory)`、`setHeader(factory)`、`setTitle(title)`
- **编辑器（5）**：`pasteToEditor`、`setEditorText`/`getEditorText`、`setEditorComponent`/`getEditorComponent`、`addAutocompleteProvider`
- **自定义（1）**：`custom<T>(factory, options?)`（overlay 支持）
- **主题（4）**：`theme`、`getAllThemes`/`getTheme`/`setTheme`
- **其他（3）**：`onTerminalInput(handler)`、`getToolsExpanded`/`setToolsExpanded`
- 对话框通用选项：`{signal?, timeout?}`（timeout 前端倒计时组件 countdow-timer 渲染）

> 注意：pi 的 `ctx.ui` **没有** `runInteractive`/`notifyDesktop`/clipboard 方法——这些是 nova 在 pi 之上的加件。

### 2.5 自定义消息类型（4 种 role）

`core/messages.ts:29-77`：

| role | 语义 / 字段 |
|---|---|
| `bashExecution` | 用户 bash 记录：`command/output/exitCode/cancelled/truncated/fullOutputPath?/timestamp/excludeFromContext?` |
| `custom` | 扩展注入：`customType`（开放字符串）/`content`/`display`/`details?`/`timestamp` |
| `branchSummary` | 树分支摘要：`summary/fromId/timestamp` |
| `compactionSummary` | 压缩摘要：`summary/tokensBefore/timestamp` |

### 2.6 对话框组件（modes/interactive/components/）

`extension-selector.ts`（select/confirm 载体，支持 timeout 倒计时）、`extension-input.ts`（input 载体）、`extension-editor.ts`（editor 载体）、`login-dialog.ts`（OAuth 等待框）、`oauth-selector.ts`、`trust-selector.ts`、`first-time-setup.ts`、`user-message-selector.ts`、`model-selector.ts`、`thinking-selector.ts`、`scoped-models-selector.ts`、`theme-selector.ts`、`settings-selector.ts`、`config-selector.ts`、`show-images-selector.ts`、`session-selector.ts(+search)`、`tree-selector.ts`

---

## 3. nova（Python 后端 + TS 前端）

### 3.1 `AgentEvent`（10 型——nova_agent 原生，与 pi 逐项对位）

`nova_agent/types/events.py`：`agent_start`、`agent_end`、`turn_start`、`turn_end`、`message_start`、`message_update`、`message_end`、`tool_execution_start`、`tool_execution_update`、`tool_execution_end`

### 3.2 Bus 2 `AgentSessionEvent`（26 型 = 10 + harness 自产 16）

`core/types/events/unions.py:81-100`：

| 事件 | 语义 |
|---|---|
| `agent_settled` | 运行彻底结算（pi 对位） |
| `auto_compaction_start|end` | 自动压缩（threshold/overflow） |
| `auto_retry_start|end` | 自动重试（attempt/maxAttempts/delayMs） |
| `model_changed` | 模型切换 |
| `queue_update` | steering/follow-up 队列变化 |
| `session_info_changed` | 会话名/角色/persona 变化（三字段全量值） |
| `session_reloaded` | 会话 reload 完成 |
| `session_replaced` | 会话切换（前端全量重 sync 触发） |
| **`user_tool`** | **用户工具进度**（信封 `{tool, event, data, callId}`；`event = start（{command, excludeFromContext}）/ output（{chunk}）`——跨进程流式投影，设计演化中拟并入 custom 生命周期） |
| `thinking_level_changed` | 思考级别变化 |
| `compaction_start|end` | 手动压缩 |
| `entry_appended` | 会话文件追加条目（custom 条目实时进转录） |
| `cache_miss` | 上下文缓存未命中检测 |
| `extension_error` | 扩展错误透出 |

### 3.3 RPC 方法（76 个，8 域）

`rpc/protocol/methods/`（注册表即方法表，schema_export 导出）：

- **session**：`initialize`、`syncSession`（P2 原子快照）、`createSession`、`getSessionState`、`getSessionEntries`（分页）、`newSession`、`switchSession`、`fork`、`navigateTree`、`compact`、`prompt`、`steer`、`followUp`、`abort`、`setSessionName`、`setSteeringMode`、`setFollowUpMode`、`getSessionStats`、`listSessions`、`deleteSession`、`cloneSession`、`exportSession`、`importSession`、`setLabel`、`appendEntry`、`getSessionAgents`、`saveAgent`、`setPersonaOverride`、`getPersonas`、`shutdown`、`dispose`、`reload`、`runCommand`、`cancelRequest`（→system）等
- **model**：`listModels`、`setModel`、`cycleModel`、`setThinkingLevel`、`cycleThinkingLevel`、`getScopedModels`、`setScopedModels`、`login`/`logout`/`getAuthStatus`（→auth）
- **auth**：provider 登录/登出、OAuth 轮询、credential 管理
- **resources**：skills/prompt templates/personas 目录
- **settings**：`getSettings`、`updateSettings`、`updateGlobalSettings`、`excludeResource`/`includeResource`
- **system**：`getCommands`、`getShortcuts`、`invokeShortcut`、`getExtensionFlags`、`setExtensionFlag`、`cancelRequest`
- **user_tools**：`invokeUserTool`（`!`/`!!` 入口）
- **package**：`listPackages`、`installPackage`、`uninstallPackage`、`updatePackage`、`getPackageInfo`、`validatePackage`

> 握手：`initialize` → `contractVersionMajor/Minor`（1/3）+ `capabilities{domains, methods}`（major 硬拒/minor 加法放行——codex 无版本，我们更严）。

### 3.4 反向原语（后端→前端）

**帧层（5）**（`rpc/ui_context.py` RoutingUIContext）：

| 帧 | 方向 | 语义 |
|---|---|---|
| `ui/request {id, component:{componentType, ...params}}` | 后→前 | 需要应答的 UI 请求（泛型——自由 method） |
| `ui/response {id, result}` | 前→后 | 应答（undefined = 用户取消 → 归一化 `UIResponse(cancelled=True)`） |
| `ui/cancel {id}` | 后→前 | 撤框（产生点收敛：仲裁统一发 + 首响应胜出败者撤） |
| `ui/notify {method, ...params}` | 后→前 | 免应答通知（set_status 等） |
| `system/capabilities {capabilities[]}` | 前→后 | 能力宣告（基线 + `dialog:*`/`host:*` 槽位，注册即重宣告） |

**词汇层**（定义权归包，`nova_coding_agent/ui_primitives.py` 定义基线）：

- **基线五件**（宿主保证）：`select`、`select_items`、`confirm`、`input`、`form`（+ `notify_message`、`set_status`）
- **`dialog:*`**（包注册，问人）：`dialog:question`（question 工具单框/多问分页）、`dialog:tools`（工具开关面板）、`dialog:interactive-shell`（终端让位）
- **`host:*`**（拟议，调宿主机器能力）：`host:openUrl`、`host:notify`、`host:writeClipboard` 等——原名 `client:*` 已修正（client 是全集；host 与 ExtensionUIAPI "宿主原语" 汇合）

### 3.5 自定义消息与渲染槽

- **`BashExecutionMessage`**（`role: "bashExecution"`，`nova_coding_agent/bash/message.py`）：`command/output/exit_code/cancelled/truncated/full_output_path?/timestamp/exclude_from_context`——`!`/`!!` 记录（MESSAGE_TYPES 注册，包缺席降级为不透明消息）
- **custom role**：扩展 `send_custom_message` 注入（`customType` 开放字符串 + `content` + `display` + `details?`）
- **会话条目类型**（SessionEntry，JSONL）：`message`、`thinking_level_change`、`model_change`、`active_tools_change`、`compaction`、`branch_summary`、`leaf`、`label`、`custom`、`custom_message`、`session_info`
- **渲染槽 9 族**（前端 slots）：`tool:<工具名>`、`entry:<customType>`、`region:<区域>`、`block:<kind>`、`editor:main`、`command:<名>`、`shortcut:<键>`、`autocomplete:<名>`、`dialog:<名>`

---

## 4. 三层对照分析

### 4.1 事件抽象

| | pi | codex | nova |
|---|---|---|---|
| 统一抽象 | `AgentEvent` 10 型直达（零翻译层） | **item 生命周期**（started/updated/completed——一种形状承载全部工作单元，user/LLM 用 `source` 分源） | 信封统一传输（`{type,data,seq,ts,sessionId}`）+ 实体形状三种（message/toolCall/custom——custom 族演化至 start/delta/end 与 item 同构） |
| 翻译层位置 | 无 | **服务器侧**（bespoke_event_handling → item 通知） | **客户端侧**（mirror mapping 归约——后端零 UI 概念，哑管道） |
| 事件序号 | — | 无（读 API 追赶） | **seq 锚点**（syncSession 高水位对账） |

### 4.2 交互词汇（反向原语）

| | pi | codex | nova |
|---|---|---|---|
| 形态 | 进程内方法（27 件） | 封闭类型化 ServerRequest（11 个，宏+生成物） | **开放泛型 1 帧**（自由 method，词汇归包） |
| 应答者区分 | 方法语义 | 类型语义（approval/elicitation=人；tool/call=机器） | **族名**（`dialog:*`=人；`host:*`=宿主机器） |
| 能力降级 | — | 无（客户端须全实现） | **有**（has_capability + 基线路径） |
| 新增成本 | 写组件 await | 改协议 + 两端发版 | **包内自产自销，框架零改动** |

### 4.3 挂起请求生命周期

| | pi | codex | nova（设计定稿） |
|---|---|---|---|
| 终结 | `Promise.race(dialog, signal)` | 中央仲裁（`cancel_requests_for_thread`，turn 清扫点） | 作用域归属 + **`agent_end` 仲裁** |
| 超时 | 无全局（per-dialog 倒计时） | 无全局（login 10min 业务超时） | 删 300s 全局；`timeout_ms` 业务语义 |
| 撤框 | race 免费 | turn 事件免费（无帧） | `ui/cancel` 帧（两点：仲裁 + 败者） |
| 挂起归属/重连 | 进程 | 线程 + **重放** | 会话 + **重放**（WS 批，seq 锚点已铺） |

### 4.4 用户命令（`!cmd`）三种解法

| | pi | codex | nova |
|---|---|---|---|
| 通道 | 零事件（进程内回调） | **与 LLM 同 item 类型**（`source=UserShell`） | `user_tool` 专用事件组 → **消亡**，并入 `BashExecutionMessage` 的 start/delta/end |
| 落盘 | custom message | rollout item 历史 | custom message（JSONL 卫生：delta 瞬态不落盘） |

---

## 5. 结论（nova 演化方向已定）

1. **custom 通道 = 包级 item 体系**：`message_start → delta（瞬态）→ message_end` 生命周期统一 bash 流式与审批留痕（`permissionDecision`），user_tool 事件组消亡（身份一贯到底，callId/类型猜测/前端缝合三笔债清零）。
2. **三分流**：审批走 custom 条目（事实化/审计/重放）；临时问答保持浮层（dialog:*，雁过无痕合理）；执行调用走 `host:*` 宿主原语族（执行位置永远在客户端）。
3. **生命周期归仲裁**：作用域归属 + `agent_end` 批量清扫（codex 对齐），删 300s 全局超时，`timeout_ms` 保留业务语义。
4. **治理替代兜底**：每个故障类追到前提消灭之（归属缺失/执行位置错误/挂起随连接死/终结靠自觉），不再加兜底层。
5. **我们独有的两件**：事件 seq 锚点（codex 无序号）与能力降级（codex 客户端须全实现）——保留。
