# Nova TUI 完整设计方案（定稿）

> 目标：**不弱于 pi**——pi interactive 的全部内置体验 + 同级的扩展 UI 自由度，
> 且为 M3（Web 宿主）保留零返工期权。
> 本文档自包含：每个关键决策附理由（均经与 pi 的逐项对照验证）。

---

## 1. 总纲：三条铁律 + 一条哲学

1. **行为归 Python**：工具执行体、命令 handler、事件拦截、agent 行为全在后端；
   **不做反向工具通道**（Node 不作为工具执行载体）。
2. **呈现与交互归 TS**：渲染器、部件、逃生舱、编辑器、对话框、主题、键位。
3. **TUI 优先，Web 保留期权**：一切过线产物是数据——M3 时零返工。

**一条哲学：过线皆数据。** Python→Node 是事件（哑管道 NDJSON），Node→浏览器
是块（blocks）——同一原则的两跳。组件实例永不过线（闭包/状态/方法序列化不了）。

---

## 2. 客户端分层（进程边界决定数据形态）

```
Python 后端（nova_harness）   ← 智能体运行时：事实源（事件/状态/执行）
   ↑ stdio（同机父子进程）
Node 层（nova-client）         ← 一级客户端：状态投影 + 渲染器执行 + 扩展宿主
   ↑ WebSocket（M3，过网）     ← 渲染产物以"块"过网
浏览器（M3）                   ← 二级客户端：块 → DOM 适配器
```

- TUI 形态：Node 层自己就是终点（块 → pi-tui 组件，同进程直挂）；
- Web 形态：块经 WS 到浏览器，适配器画 DOM——**新包上线客户端零下载**
  （词汇适配器早已内置，渲染器产出全落在已有词汇里）。

---

## 3. 模块化结构（防 6000 行编排层——pi 的教训）

pi 的组件是模块化的，但编排层没收住（interactive-mode.ts 6000 行）。
我们的对策：**controllers/ 分层 + app.ts 只装配**。

```
modes/tui/
├── main.ts               # CLI 入口（commander）
├── app.ts                # 装配根：创建组件树 + 接线 controllers + 启动。零业务逻辑
├── controllers/          # 编排层（每域一个）
│   ├── keymap.ts         # 全局键位路由（Esc 域级分派/ctrl 族，键位表驱动）
│   ├── editor.ts         # 编辑器接线（submit/粘贴折叠/图片/补全/历史）
│   ├── dialogs.ts        # 对话框调度（四件套 + auth 等待框 + ui/cancel）
│   ├── transcript.ts     # transcript 调度（reconcile/展开折叠/customType 路由）
│   ├── status.ts         # 状态区调度（指示器家族/footer/通知槽位）
│   └── pickers.ts        # 选择器调度（picker 字段路由 → 专用选择器）
├── components/           # 纯组件：自包含、自渲染、零编排逻辑
│   ├── transcript/       # 条目视图族（含 skill-invocation 折叠条目）
│   ├── dialogs/          # 四件套 + auth-waiting
│   ├── pickers/          # 选择器族（基础件 + searchable 基类 + hints）
│   ├── status/           # StatusIndicator 基类 + 变体 + countdown-timer + footer
│   └── layout/           # dynamic-border + welcome 启动区
├── blocks/               # 块适配层（注册制：官方五块 builtin 注册 + schema 校验）
├── themes/               # 主题系统（dark/light + 自定义目录 + /theme 预览）
├── keymap/               # 键位系统（表 + JSON 三级合并 + hints + restrictOverride）
└── utils/                # clipboard（图片/文本——编辑器增强验证下来均在 pi-tui
                          # 内建：粘贴折叠/undo/kill-ring 零代码，故无 editor/ 目录）
```

**三条硬纪律**：
1. `app.ts` 只做装配——任何逻辑判断进对应 controller；
2. 组件零编排——自管渲染与自身输入，组件间协作（对话框开/焦点转移）经 controllers；
3. 路由经注册表——选择器调度、条目路由、主题切换全部查表；
   新增一个选择器/视图/主题 = 加一行注册，不是改 if-else 链。

---

## 4. 呈现体系核心：块注册制（blocks as registered slots）

**一切上屏内容都是注册的块**——slots 加 `block:<kind>` 族键：

```ts
interface BlockDefinition {
  schema?: Schema;                        // 数据形状声明（渲染前校验）
  renderTui?: (data, ctx) => Component;   // TUI 适配器（Node 层产 pi-tui 组件）
  renderWebEntry?: string;                // Web 适配器 bundle 路径（M3 过网下载）
}
slots.registerBlock('diff', def, source);   // source: 'builtin' | 包名
```

- **官方块**（diff/markdown/code/table/text/image）→ `builtin` 注册——
  现有 `blocks/` 的硬编码 switch 适配器**迁移为 builtin 注册**（dogfood 同一机制）；
- **自定义块**（扩展注册）→ 包注册：`schema` + 自带适配器组件；
- **校验**：块数据渲染前过 schema——扩展产出对宿主是不可信输入
  （与后端"校验只给不可信输入"同一原则），坏数据在边界拦截，不炸组件树。

### 数据流

```
渲染器/扩展产出块 [{kind, ...data}]
  → 宿主查 slots block:<kind> 适配器
  → schema 校验（声明则验，坏数据拦截并诊断）
  → 适配器渲染：TUI 产 pi-tui组件 / Web 经 bundle 产 DOM
```

### 为什么需要块（决策理由，经多轮推敲定案）

- **过线硬约束**：Web 形态下渲染产物必须过网（WS 传数据）——组件实例过不了；
  块是"渲染产物的可序列化形态"。TUI-only 时它是捷径，考虑 Web 它是必需；
- **双端复用**：渲染器写一次产块，TUI 画 pi-tui、Web 画 DOM；
- **生态友好**：渲染细节（diff 红绿行/词级高亮/截断）在适配器写一次，
  全部渲染器共享；作者零终端知识（返回数据即可）；
- **版本解耦**：渲染器只依赖 NovaBlock 类型（纯 type），不被 pi-tui 版本绑死；
- **词汇封闭、组件开放**：官方词汇固定（稳定才可内置）；
  自定义呈现走"自定义块注册"（数据形状与适配器都归包作者）——
  词汇表管不着它（宿主按注册的 component 路由，不认识数据形状）。

### 两层自由度（③沙箱已砍——理由见下）

| 层 | 形态 | 作者写 | 校验/信任 |
|---|---|---|---|
| ① 官方块 | builtin 注册（内置适配器） | 返回块数据 | schema 内置 |
| ② 自定义块（主力自由出口） | 包注册 + 自带适配器：TUI 产 pi-tui 组件直挂（= pi `custom` 同级）；Web 产浏览器组件 bundle 过网直挂宿主容器（React/Vue/原生任选，框架自含） | 自定义数据 + 自写适配器 | **注册 schema 校验** |

**③iframe 沙箱已砍**：trust 门控 fail-closed（不信任的包不加载）+ schema 校验
（坏数据进不来）已覆盖安全需求；"展示不信任外部 HTML"的边缘场景将来作为
官方块的一个 kind（`html-preview`，适配器内部用 iframe）后补——是词汇加法，
不是独立一层。

---

## 5. 六大内置系统（TUI 完整度，pi 对位）

### 5.1 选择器系统（最大缺口）

- **基础件** `pickers/selector.ts`：复刻 pi `ExtensionSelectorComponent`
  （标题 + DynamicBorder + SelectList + 可选 countdown + 键位提示行）；
- **搜索基类** `pickers/searchable.ts`：输入行 + pi-tui `fuzzy` 过滤 + 计数；
- **具体选择器**：session（/resume：搜索+时间+消息数）、model（provider 分组+
  当前标记）、user-message（/fork 目标）、tree（/tree 层级）、theme、thinking；
- **接线**：`ui.select` 通道带 **`picker` 提示字段**（零契约变更）——
  pickers controller 按它路由到专用选择器；无该字段走默认 SelectList。

### 5.2 编辑器系统

- 粘贴折叠（pi-tui editor 内建 paste markers——验证启用 + 提交时
  `getExpandedText()` 展开）；图片输入（`[image]` 标记 → ImageContent 上送）；
- undo/kill-ring（pi-tui 内建启用）；补全加描述列。

### 5.3 状态指示系统

- `StatusIndicator` 基类（extends pi-tui Loader）+ 四变体：
  working / retry（**CountdownTimer 倒计时**："Retrying (2/3) in 5s… (esc to cancel)"）
  / compaction（带 reason）/ branchSummary；
- 数据：store.status 四态 + `auto_retry_start` 的 attempt/max/delay（事件直写 store）；
- `CountdownTimer` 独立组件（对话框 timeout 倒计时复用——transport 的
  `timeout_ms` 已就绪）。

### 5.4 键位系统

- `keymap/`：内置键位表 + `~/.nova/agent/keybindings.json` + 项目级
  `.nova/keybindings.json`（合并优先级 project > user > builtin）；
- **restrictOverride**（pi 同款：保留键位禁覆盖 / 可覆盖带诊断）；
- **keybinding-hints**：选择器/对话框底部提示行从键位表动态生成；
- Esc 域级路由、ctrl+c/ctrl+d/ctrl+o 全部迁入键位表（可配置）。

### 5.5 主题系统

- 多主题 JSON（dark 现有 + light + pi 移植）；
- `/theme` 命令（session_commands 加）+ theme 选择器（预览）；
- 持久化：经 RPC settings 通道（settings 加 theme 字段，若无则加）。

### 5.6 命令交互系统

- **双 Esc**：空编辑器双击 Esc → /tree 或 /fork（`doubleEscapeAction` 设置：
  tree/fork/none 三档）；
- **welcome** 启动区（版本/模型/cwd）；
- **first-time-setup**：无模型时引导 login/选模型。

---

## 6. 扩展 UI：`ui/index.ts` 宿主

resources/loader.ts 加载 `ui/index.ts`（`hasExtensionEntry` 发现已就位）→
`factory(api)`：

```ts
export default function (api: ExtensionUIAPI) {
  api.registerRenderer('my-tool', render);             // 工具渲染器（产块）
  api.registerBlock('rna-structure', def);             // ②自定义块（schema+适配器）
  api.registerRegion('footer', (ctx) => blocks);       // 区域部件
  api.registerEditor(factory);                         // 扩展编辑器
}
```

**边界**：行为注册（命令/工具/事件）不经此 API——归后端 Python 扩展
（自动重命名/白名单/事件合并已是 pi 同构的完整模型）。

---

## 7. 施工批次（每批独立可用）

| 批 | 内容 | 依赖 | 状态 |
|---|---|---|---|
| **P0-1** | controllers/ 分层落地 + 选择器系统（基础件+searchable+session/model/user-message） | 无 | ✅ |
| **P0-2** | 状态指示器家族 + CountdownTimer | 无 | ✅ |
| **P0-3** | 编辑器系统（粘贴折叠/图片/undo/kill-ring 验证启用） | 无 | ✅ |
| **P1-1** | 键位系统（keymap/ + JSON + hints + Esc 迁入） | 无 | ✅ |
| **P1-2** | 主题系统（多主题 + /theme + 持久化） | P0-1 | ✅ |
| **P1-3** | 双 Esc + welcome + first-time-setup | P0-1 | ✅ |
| **P2-1** | **块注册制**：slots 加 block 族 + 官方块迁移 builtin 注册 + schema 校验 | 无 | ✅ |
| **P2-2** | `ui/index.ts` 宿主 + region 部件 | P2-1 | ✅ |
| **P2-3** | 自定义块（扩展注册+适配器）+ 扩展编辑器 | P2-2 | ✅ |
| **P2-4** | settings 可视化编辑 + skill 消息/条目补全 | P1 | ✅ |

---

## 8. 验收标准（"不弱于 pi"的判据）

pi 用户切到 Nova TUI 找不到任何"以前能、现在不能"的操作：

- 选择器可搜索可预览；retry 有倒计时；粘贴折叠；图片可发；
- 键位可配；主题可换；双 Esc 导航；编辑前可见 diff（已有）；
- 包作者：一行数据渲染 diff（①）、自定义呈现组件（②）、
  footer 加内容（region）、全自由面板（逃生舱）；
- 且：数据可校验（schema）、覆盖可诊断（collision）、来源可在案（source）。

---

## 附：关键决策记录（本轮讨论定案）

| 决策 | 结论 | 理由 |
|---|---|---|
| 反向工具通道 | **不做** | 工具执行永远 Python；Node 不是执行载体 |
| blocks | **保留**（TUI 是捷径，Web 是必需） | 过线硬约束 + 双端复用 + 生态友好 + 版本解耦 |
| 块词汇 | **封闭官方词汇 + 注册制扩展** | 稳定才可内置；自定义走注册（带校验） |
| iframe 沙箱层 | **砍掉** | trust 门控 + schema 校验已覆盖；html-preview 块后补 |
| 逃生舱 | ②自定义块（TUI 组件直挂 = pi 同级） | 全自由出口；Web 形态 bundle 过网 |
| trust 门控位置 | 编排层（发现域过滤） | pi 编排思想：不信任的源不进发现域 |
| slots 覆盖 | 后注册覆盖 + collision 诊断 | 高优先级源赢；覆盖必须可见 |
| slots reload | 整体替换（新建 SlotRegistry） | 对齐后端 ToolsManager.refresh 原子形态，防残留 |
| 键位 | 可配置（JSON 合并）+ 保留键位 | pi KeybindingsManager 对位 |
