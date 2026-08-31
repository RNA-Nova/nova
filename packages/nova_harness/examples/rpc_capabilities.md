# nova_harness RPC 能力清单与评估

> 状态：2026-07 哑管道重构后的第二轮定格版（64 个注册方法 + 2 条协议直管消息）。
> 范围：`rpc/`（transport / protocol / server）+ `modes/rpc`。
> 读者：前端（nova-client 及将来第三方前端）的接入方。

---

## 1. 设计原则（读懂全表的前提）

**哑管道**：RPC 传输运行时的事实，不传输渲染的意图。线上没有第二套事件
词汇、没有块字典、没有 UI 文案——线上契约 = 运行时事件类型本身。
契约的机器化（多后端可插拔的根基）：

- **事件/条目类型** → `rpc/protocol/schema_export.py` 构建期导出
  `nova-wire.schema.json` + `nova-wire.gen.ts`（漂移测试保鲜）；
- **方法形状** → `methods/shapes.py` 在注册处声明 params/result 模型：
  分派前校验（缺参/类型错误 → `INVALID_PARAMS`，布尔用 StrictBool 防宽松强转）、
  schema `methods` 根、TS `NovaWireMethodMap`，三方同源零漂移；
- **契约版本** → `CONTRACT_VERSION_MAJOR/MINOR`：schema 工件、`initialize` 握手、
  TS 常量三处同源——major 不等硬拒；minor 加法放行（能力位与未知事件忽略降级）；
- **能力位** → `initialize.capabilities.domains/methods` 来自注册表实况
  （方法域可选宣告——其他语言的后端可只实现部分域，前端按位降级）；
- **一致性套件** → `tests/conformance/`：spawn 真实后端、管道另一端全双工
  驱动 + schema 校验（黑盒"入网认证"，v1 不依赖 LLM）。

四股流（按"谁发起、请求流向哪边"分）：

| 流 | 方向 | 内容 |
|---|---|---|
| 事件 | 后 → 前（通知，无应答） | Bus 2 全量直通：`agent/event`，params 为 `{type, data}` 信封 |
| 命令 | 前 → 后（请求-响应） | 运行时 API 薄包装（68 个方法，下表） |
| 快照 | 前 → 后（请求-响应） | `getSessionState` + `getSessionEntries`，重连全量恢复 |
| 反向原语 | 后 → 前（请求-响应） | 后端索要数据：`ui/request` ↔ `ui/response` + `system/capabilities` |

协议自查三问（新场景验收）：是变更吗 → 命令；会让状态变吗 → 事件；新连上的前端要知道吗 → 快照。

**键位的位置**（容易想错）：键位捕获、内置键位表、用户自定义与冲突裁决归
前端；运行时只持有扩展 shortcut 的**注册表与 handler**。过线两条：目录
（`getShortcuts`）与回调（`invokeShortcut`）。后端永远不知道用户按的是
哪个物理键——包括 ctrl+c/ctrl+d（raw 模式下只是字节，由前端翻译为
`abort`/`shutdown`）。

## 2. 命令方法表（64 个，按域）

### session 域（39）——会话生命周期、turn 控制、队列、历史

| 方法 | 能力 |
|---|---|
| `initialize` | 握手（版本 + 能力位） |
| `createSession` | 创建 runtime + 会话（cwd/model/agentName/sessionFlag/continueLast） |
| `newSession` | 新会话（保留 runtime） |
| `switchSession` | 切换到既有会话（path 绝对路径或 sessionId 解析），无需重建 runtime |
| `listSessions` | 磁盘会话列表（id/name/path/mtime，供恢复选择） |
| `cloneSession` | 克隆当前会话到新文件并切换 |
| `exportSession` / `importSession` | 会话 JSONL 导出 / 导入并切换（分享/备份/迁移） |
| `dispose` / `shutdown` | 释放 runtime / 关停服务器 |
| `prompt` | 发送用户消息（text/images，长命令发起即返回） |
| `steer` | turn 中插入 steering（当前 turn 后、下次 LLM 调用前送达） |
| `followUp` | 排队 follow-up（空闲后处理） |
| `abort` | 中止当前 turn/流式/压缩/分支摘要/用户工具/重试 |
| `setSteeringMode` / `setFollowUpMode` | 队列模式（all / one-at-a-time），持久化 settings |
| `clearQueue` | 清空 steering + follow-up 队列，返回被清内容 |
| `setSessionName` | 重命名（持久化 + `session_info_changed` 事件） |
| `setLabel` | entry 标签设置/清除（transcript 导航锚点） |
| `setActiveTools` | 激活工具集（同步重建 system prompt） |
| `abortRetry` / `setAutoRetry` | retry 手动控制（中止进行中的重试 / 开关自动重试） |
| `abortCompaction` / `setAutoCompactionEnabled` | 压缩控制（域级中止进行中的压缩 / 开关自动压缩） |
| `reload` | 热重载资源/扩展/settings |
| `navigateTree` | 会话树跳转（entryId，可带分支摘要选项） |
| `fork` | 按 entryId 分叉新分支会话 |
| `compact` | 手动压缩（进度走 compaction 事件流） |
| `changeAgent` | 切换 agent（白名单/工具/提示词全链重建） |
| `saveAgent` | 物化当前生效状态为组合声明 yaml（/agent save 的 RPC 面；包来源影子写 user 级，可选 name 做 save-as） |
| `listAgents` | 已安装 agent 名录 |
| `getSessionState` | **快照**：model/thinking（级别 + supports + 可用级别表）/active_tools/队列/is_streaming/is_compacting/is_retrying/auto_retry/auto_compaction/队列模式/pending 计数 |
| `getSessionEntries` | **全保真历史**：条目全集（id/parent_id/type/原始 payload）——transcript 重建、树导航、标签的统一数据源 |
| `getSessionStats` | 统计（消息计数/tokens/cost/**cache_waste** 缓存浪费分析） |
| `getContextUsage` | 上下文窗口用量估算 |
| `getTools` | 注册表工具目录（name/description/parameters/prompt 元数据/来源） |

### model 域（7）——模型发现与切换

| 方法 | 能力 |
|---|---|
| `listModels` | 全量模型（内置 + models.json + 扩展注册）× 可用性标记 × reasoning 能力位 |
| `setModel` | 切换（'provider/model_id' 或完整 Model dict；字符串走**会话** model_runtime，扩展注册的 provider 可见） |
| `setThinkingLevel` | 思考等级（off…max 显式枚举校验，非法值报错；持久化 + 事件） |
| `cycleThinkingLevel` | 循环切换思考级别（模型不支持时 ok=false） |
| `cycleModel` | scoped 集合内轮询（forward/backward） |
| `listScopedModels` / `setScopedModels` | scoped 集合读写（模型循环的作用域） |

### auth 域（4）——鉴权

| 方法 | 能力 |
|---|---|
| `getAuthStatus` | credential 元信息（provider + 类型，**不含密钥本体**）；无会话也可查（agent_dir 绑定） |
| `login` | 交互式登录统一入口（OAuth device code / ApiKey prompt；交互经反向原语 `ui/select`/`ui/input`；长命令，`ModelRuntime.login` 模型刷新联动） |
| `setApiKey` | 直接设置 API key（持久化 + `ModelRuntime.refresh()` 联动） |
| `logout` | 删除 credential 并联动模型刷新/可用性快照（`ModelRuntime.logout`） |

> 写操作统一走 `ModelRuntime` 联动路径（credential 变更 → 模型刷新 + 可用性快照重算）。

### settings 域（2）——用户设置读写

| 方法 | 能力 |
|---|---|
| `getSettings` | 合并生效配置（global + project 深合并，None 省略）；**createSession 前即可读**（懒加载 fallback 管理器） |
| `updateSettings` | 全局层部分更新（`Settings` 模型校验，未知键/类型错误拒绝，显式 null 清除） |

> settings schema 分两类字段：运行时消费的（模型/重试/压缩/shell/包列表等）
> 与纯前端消费的展示偏好（editor/cursor/autocomplete/changelog 等）——后者
> 运行时只存储与 round-trip，从不解释语义；键位、主题等前端状态同理，
> 由前端经本通道或自持文件持久化。

### resources 域（2）——前端菜单数据源

| 方法 | 能力 |
|---|---|
| `listPromptTemplates` | prompt templates（name/description/argument_hint/来源） |
| `listSkills` | skills（name/description/file_path/来源） |

### system 域（6）——命令、扩展 flags、快捷键与调用取消

| 方法 | 能力 |
|---|---|
| `getCommands` | 三源合并的 slash 命令目录（扩展命令 + prompt templates + skills） |
| `getShortcuts` | 扩展注册的快捷键目录（键名/描述/来源；扩展间冲突诊断走扩展诊断事件） |
| `invokeShortcut` | 前端键位捕获后的回调：分发到扩展 handler（异步执行，错误走扩展错误事件） |
| `getExtensionFlags` | 扩展注册的 CLI flags（定义 + 当前值） |
| `setExtensionFlag` | 设置 flag 值（未知名拒绝） |
| `cancelRequest` | 按 RPC request id 取消在飞调用（LSP `$/cancelRequest` 的方法版：幂等应答 `{cancelled}`；CancelledError 沿 await 链穿透，server 写回 -32800——长命令如 OAuth 登录的 Esc 入口；run 的取消仍走 `abort*` 域方法） |

### user_tools 域（3）——用户工具

| 方法 | 能力 |
|---|---|
| `listUserTools` | 目录（name/description/parameters/来源） |
| `invokeUserTool` | 调用（进度走 `user_tool` 事件，结果记为自定义消息） |
| `abortUserTool` | 中止执行中的用户工具 |

### package 域（6）——包管理（operator-facing optional domain）

> **运营可选域**：服务"应用内包管理面板"形态与远程前端，**会话核心不需要**；
> 开发者的主通道始终是 `nova-pkg` CLI（同一 `PackageManager` 核心，无第二份实现）。
> 其他语言的后端可不宣告本域（pip/uv 语义不可移植），前端按能力位隐藏入口。

| 方法 | 能力 |
|---|---|
| `pkgList` / `pkgInfo` | 已安装包列表 / 详情 |
| `pkgInstall` / `pkgUninstall` / `pkgUpdate` | 安装 / 卸载 / 更新（进度走 `package_progress` 通知；阻塞调用经线程执行，不冻结 abort/应答） |
| `pkgCheckUpdates` | 只读更新检查（前端启动拉取；离线/失败静默返回空列表） |

### 协议直管消息（2，不进方法表）

以下两条由 `NovaServer._handle_ui_inbound` 在分派前直接处理，是反向通道
的前端侧，不是可调用方法：

| 消息 | 能力 |
|---|---|
| `ui/response` | 前端应答反向原语请求（按请求 id 完成挂起的 future） |
| `system/capabilities` | 前端上报支持的原语子集（不支持的走文档化兜底） |

## 3. 事件面（bus2 哑直通）

- 信封：`agent/event` notification，params = `{"type": <运行时事件 type>, "data": <model_dump 原样>}`；
- 覆盖：agent/turn/message（start/update/end）、tool_execution（start/update/end）、
  compaction（auto/manual start/end）、auto_retry、queue_update、model_select、
  thinking_level_select、session_info_changed、user_tool、扩展事件——**全量，不重不漏**；
- 唯一加工：JSON 安全兜底（AbortSignal/Callable → str），属传输本分；
- session 替换（switch/fork）时桥自动 rebind 重订阅，旧会话事件即刻停止转发。

## 4. 服务器工程特性

- **并发分派**：每条入站消息独立 task——turn 进行中的长 prompt 不阻塞 abort/steer；
  包管理等阻塞型命令经 `asyncio.to_thread` 执行，不冻结事件循环；
  在飞调用登记 id→task 映射（`ServerState.request_tasks`），`cancelRequest` 按 id 寻址取消；
- **写锁串行化**：并发分派下 JSON-RPC 帧不撕裂；
- **关停语义**：SIGINT/SIGTERM/SIGHUP → 先 kill 被跟踪的 detached 子进程再关服务器；连接断开时取消进行中的命令任务；
- **OutputGuard**：stdio 模式下保护协议通道不被杂散 stdout 污染；
- **Transport 抽象**：stdio 主力 + memory（测试）；WebSocket 归 Node 层（架构 2.0，Python 永不直接暴露网络端口）。

## 5. pi 前端能力对照

| pi 前端能力 | 覆盖 | 通道 |
|---|---|---|
| 发送/steer/followUp/abort | ✅ | prompt/steer/followUp/abort |
| 队列模式切换 + pending 管理 | ✅ | setSteeringMode/setFollowUpMode/clearQueue + 快照 |
| 模型选择器（发现 + 切换 + 循环 + scoped） | ✅ | model 域（listModels 含 reasoning 位） |
| 思考等级（含循环切换） | ✅ | setThinkingLevel/cycleThinkingLevel + 快照可用级别表 |
| 会话命名/列表/恢复/切换/fork/树导航 | ✅ | setSessionName/listSessions/createSession/switchSession/fork/navigateTree |
| 会话克隆/导出/导入 | ✅ | cloneSession/exportSession/importSession |
| 工具目录/激活切换 | ✅ | getTools/setActiveTools |
| 用户工具（pi user_bash 超集） | ✅ | user_tools 域 |
| compact + 上下文用量 | ✅ | compact/getContextUsage |
| slash 菜单（命令 + 模板 + skills） | ✅ | getCommands/listPromptTemplates/listSkills |
| 扩展快捷键（目录 + 执行） | ✅ | getShortcuts/invokeShortcut（键位绑定与裁决归前端） |
| entry 标签 | ✅ | setLabel + getSessionEntries |
| 统计与 cache 浪费 | ✅ | getSessionStats（含 cache_waste） |
| trust/OAuth/扩展询问 | ✅ | 反向原语 + capabilities |
| 扩展 flags | ✅ | getExtensionFlags/setExtensionFlag |
| 包管理（运营可选域） | ✅ | package 域（含 pkgCheckUpdates 启动更新拉取） |
| OAuth 交互登录流程 | ✅ | login（反向原语交互） |
| retry 手动控制 | ✅ | abortRetry/setAutoRetry |
| 设置读写（远程前端，含启动前） | ✅ | getSettings/updateSettings（无会话可用） |

## 6. 挂账（已知缺口，按优先级）

1. **nova-tui 协议断代**：旧映射协议（tool_call 顶层字段、getSessionMessages）已被
   哑管道取代，nova-tui 未同步修复——由 nova-client 接替，不再投资。

> 已解决：~~构建期 schema 导出~~——`rpc/protocol/schema_export.py` 从
> Python 事件（Bus 2 全集）/会话条目类型生成 `nova-wire.schema.json` +
> `nova-wire.gen.ts` 双工件（入仓 + 漂移测试），nova-client 的
> mapping/store/rpc-client 已全面采用生成类型。

## 7. 评估结论

命令面 68 个方法覆盖 pi 前端能力面的**全部主干**（本轮补齐：会话切换/克隆/导出导入、
思考级别循环、扩展快捷键目录与回调、启动前 settings/auth 读取）；事件面全量直通，
前端能拿到的信息严格多于旧协议，且不再夹带 UI 词汇。
键位体系按架构 2.0 正确分层：捕获/绑定/裁决在前端，注册/目录/执行在运行时。
线上契约已有构建期类型导出（JSON Schema + TS 双工件 + 漂移测试），
剩余缺口全是"有通道、未接线"的小事，无结构性缺陷。
