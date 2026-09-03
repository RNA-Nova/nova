# Nova vs pi：API 与 Context 家族全量对比

> 汇总两家扩展体系的全部 API 对象与 Context 家族。
> 细读：`nova_vs_pi_api_ctx.md`（挂载点）、`nova_vs_pi_extension_events.md`（事件契约）、
> `nova-tui/docs/ui-primitives.md`（UI 原语体系终案）。

## 一、家族总览

**pi（单进程，一个上帝基座 + 变体）**

```
ExtensionAPI（pi 对象）
  ├─ 事件订阅 on()（33 个 typed 重载）
  ├─ 注册：registerCommand/Shortcut/Flag/Tool/MessageRenderer/EntryRenderer/Provider
  ├─ 动作：sendMessage/sendUserMessage/appendEntry/exec/setActiveTools/setModel/…（14 个）
  └─ events 总线

ExtensionContext（上帝，事件 handler 与工具 execute 共用）
  ├─ ExtensionCommandContext（+会话控制：newSession/fork/switchSession/reload…）
  │    └─ ReplacedSessionContext（+sendMessage/sendUserMessage）
  ├─ ProjectTrustContext（收窄：cwd/mode/hasUI + ui Pick 四件套）
  ├─ ToolRenderContext（渲染专用：args/lastComponent/state/expanded/isError…）
  └─ ExtensionUIContext（胖，~28 方法：四件套 + 组件直驱）
```

**nova（分层，按角色收窄）**

```
NovaExtensionAPI（nova 对象，装载期注册面）
  ├─ 事件订阅 on() / on_input()
  ├─ 注册：registerCommand/Shortcut/Flag/SpawnHook/Provider
  └─ events 总线
  （运行期动作全部在 ctx——api 不挂动作）

ExtensionContext（运行面：动作 + 环境）
  ├─ ExtensionCommandContext（+会话控制：fork/newSession/switch/clone/export…）
  ├─ ProjectTrustContext（收窄：cwd/has_ui/ui）
  ├─ ToolContext（工具构造期：cwd/settings）
  ├─ ToolExecContext（工具执行期：model + ui 泛型通道）
  ├─ UIContext（泛型 transport：capabilities/request/notify，零词汇）
  └─ UserTool：session 注入（构造期）
```

## 二、API 对象对比（pi 的 `pi` vs nova 的 `nova`）

| 能力 | pi ExtensionAPI | nova NovaExtensionAPI | 说明 |
|---|---|---|---|
| 事件订阅 | `on(event, handler)`（33 typed 重载） | `on(event_type, handler)` + `on_input` 别名 | pi 类型重载是 TS 红利；nova 裸字符串 |
| 注册命令 | registerCommand | registerCommand | ✅ |
| 注册快捷键 | registerShortcut | registerShortcut | ✅ |
| 注册 flag | registerFlag / getFlag | registerFlag / getFlag | ✅ |
| 注册 provider | registerProvider / unregisterProvider | 同名 | ✅ |
| **注册工具** | **registerTool** | ❌ | 归 M4 反向工具通道 |
| **注册渲染器** | **registerMessageRenderer / registerEntryRenderer** | ❌ | 归 M4 `slots.register("entry:*")` |
| spawn hook | ❌（经 registerTool 替换 bash 传 spawnHook） | **registerSpawnHook** | nova 反超：process 层一等公民 |
| **运行期动作**（14 个：sendMessage/exec/setActiveTools/setModel/…） | **在 api**（闭包调用） | **在 ctx**（ExtensionContext） | 核心分工差：pi 按"能力类型"切，nova 按"生命周期"切（注册归 api，动作归 ctx） |
| 扩展间总线 | events | events | ✅ |

## 三、CTX 家族对比总表

| 场景 | pi | nova | 对齐度 |
|---|---|---|---|
| 事件 handler 上下文 | `ExtensionContext`（上帝：ui+sessionManager+modelRegistry+model+动作） | `ExtensionContext`（动作+环境+窄 ui；无 sessionManager 写/modelRegistry） | ⚠️ 语义同构，nova 收窄 |
| 命令 handler 上下文 | `ExtensionCommandContext`（+fork/newSession/switch/reload） | `ExtensionCommandContext`（+fork/new_session/switch/clone/export/import/trust_project/untrust_project/get_session_info） | ✅ nova 更宽（pi 的 clone/export/trust 是内置命令独占） |
| 会话切换后回调 | `ReplacedSessionContext`（+sendMessage/sendUserMessage） | ❌ 无 | 小缺口（nova 的 send_message 本就在 ctx，切换后直接用） |
| trust 裁决上下文 | `ProjectTrustContext`（cwd/mode/hasUI/ui Pick 四件套） | `ProjectTrustContext`（cwd/has_ui/ui） | ✅（nova 删了 mode 字段：has_ui+capabilities 替代） |
| **工具执行上下文** | **`ExtensionContext`（上帝本帝）** | **`ToolExecContext`（model + ui 泛型通道）** | ⚠️ 哲学差：pi 全给，nova 收窄 |
| 工具构造上下文 | ❌（pi 工具定义是对象字面量 + cwd 闭包） | `ToolContext`（cwd/settings 只读视图） | nova 多一层（显式构造期注入） |
| 工具渲染上下文 | `ToolRenderContext`（args/lastComponent/state/invalidate/expanded/isError…） | `RendererInput`（纯数据：toolName/args/status/partial/result） | ⚠️ 哲学差：pi 给渲染器组件级状态，nova 只给数据 |
| 用户工具上下文 | （pi 无独立用户工具层） | `UserTool.__init__(session)` | nova 特有（user tool 类目） |

## 四、UI 上下文专项（胖 vs 窄）

| | pi ExtensionUIContext | nova UIContext |
|---|---|---|
| 宽度 | ~28 方法（四件套 + widget/footer/header/custom/theme/editor/键位…） | 泛型 4 成员（capabilities/has_capability/request/notify） |
| 对话框选项 | `{signal, timeout}`（中止/倒计时） | 无（timeout_ms 约定挂账）；abort 竞速内建（ScopedUIContext 语义在 transport 层） |
| 组件直驱 | `custom`/`setWidget`/`setFooter`/`setEditorComponent`（传工厂） | ❌ 永不在 Python 侧（函数过不了 RPC）→ 归 M4 TS 扩展 |
| 降级模型 | 每方法各自空实现（noOp 28 个空壳）；RPC 永远乐观（无协商，客户端不答即挂） | 能力集驱动（∅ 即全降级）+ 协商握手 + 300s 全局超时 |
| 收窄先例 | ProjectTrustContext 自己收窄为 Pick 四件套 | 整个后端就是窄层 |

## 五、关键结构差异（一句话各）

1. **动作面归属**：pi 挂 api（闭包），nova 挂 ctx（参数）——nova 按生命周期切（注册/运行），pi 按能力类型切（且自破规则：ctx.abort/compact/shutdown 是动作）。
2. **工具上下文**：pi 给工具上帝 ctx（execute 第 5 参就是 ExtensionContext），nova 给收窄的 ToolExecContext（model + ui 通道）——nova 的"工具即纯执行器"定位；pi 的"工具作者自律"定位。
3. **渲染上下文**：pi 的 ToolRenderContext 把组件级状态（lastComponent/内部 state）直给渲染器；nova 的 RendererInput 只给数据卡片——nova 渲染器是纯函数（跨宿主），pi 渲染器是有状态组件工厂（锁 TUI）。
4. **收窄/加宽变体**：两家都有（pi 的 Command/Replaced/Trust，nova 的 CommandContext/Trust）——这部分逐字段对齐。
5. **nova 独有**：ToolContext（构造期注入）/ UserTool session 注入 / registerSpawnHook / prepare_next_turn·should_stop_after_turn（loop 钩子）；**pi 独有**：registerTool / registerMessageRenderer / ReplacedSessionContext / 胖 UIContext 全体（归 M4）。

## 六、结论

两家都有 ctx 家族，但**组织哲学相反**：pi 一个上帝基座到处用（单进程无害，信任作者）；nova 按角色收窄（分层必要——工具/渲染器可能是第三方包代码，边界小=事故面小）。能力面差距全部有明确的归属（registerTool/渲染器注册 → M4；胖 UIContext → M4 TS 扩展层），无结构性缺失。
