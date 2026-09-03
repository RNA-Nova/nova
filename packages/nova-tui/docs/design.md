# nova-tui（含内置 TUI 宿主 modes/tui）设计文档（v3.1）

> 状态：**设计定案（v3.1，白纸重设计 + 扩展面补全）**。
> 总目标（不变）：**nova-tui 是 pi 同等能力的 TS 产品运行时——智能体语义
> 经 RPC 可插拔（Python 参考实现，可换语言），其余能力 TS 侧全量拓展，启动入口在 TS。**
> v3 架构决策：**扩展即组织原则（内建能力 dogfood 同一 API）、slot 统一内容贡献抽象、
> bus 作为全层脊柱**。v3.1 补全：**扩展 settings/state 一等公民、块词汇开放、
> 包模型全规格、包面板 dogfood**。
> 上层图纸：`nova_architecture_2.0.md`（三层模型）、`nova_architecture_2.1.md`（契约）。
> 落地顺序：本文档 §16。

---

## 1. 总目标与边界

pi 的 coding-agent 能力全集的二分（不变）：

| pi 的能力 | 归属 |
|---|---|
| 智能体语义：loop / 工具执行 / 会话树 / compaction / 模型鉴权 | **nova_harness（可换语言）**，唯一实现，TS 经 RPC 调用不重实现 |
| 扩展行为钩子（拦截式） | **Python 扩展系统**（Bus 3，进程内） |
| 其余一切：呈现推导 / TS 扩展 runtime / 渲染器 / 部件 / 主题 / 键位 / 命令 | **nova-tui**（含内置 TUI 宿主 `modes/tui`） |

**头号禁令：语义两地分居。** 智能体语义的答案永远只有一个出处（后端）；
本层的状态是它的可校准投影（快照随时对账）。

---

## 2. 总体结构：八个子系统 + 一条脊柱

```
                        ┌────────────────────────────────────┐
                        │  hosts（前端接入）                  │
                        │  in-process（modes/tui）· ws（M3）   │
                        └──────────────▲─────────────────────┘
                                       │ RuntimeHost API
   ┌──────────┐   命令/快照   ┌────────┴─────────┐
   │ 后端      │ ◄──────────► │ wire             │
   │ (可插拔)  │   事件/原语   │ client·协议·能力位 │
   └──────────┘              └────────┬─────────┘
                                      │ 原始事件/响应
                                      ▼
                              ╔═══════════════╗
                              ║  bus（脊柱）   ║  观察式 pub/sub
                              ╚═══╦═══╦═══╦═══╝
              ┌───────────────────║───║───║───────────────────┐
              ▼                   │   │   │                   ▼
        mirror（会话镜像）         │   │   │         extensions（扩展 runtime）
        事件→投影 store           │   │   │         内建与第三方同一 API
              │                   │   │   │            │        │
              ▼                   │   │   │     发现/重载    配置/数据
        presentation（呈现）◄─────┘   │   │   │            │        │
        slot 注册表 + 声明式块          │   │   ▼            ▼        ▼
              │                       │  packages（包）    settings（配置/数据）
              │                       │  索引/资产/生命周期  ui-settings/state
              └──► 渲染模型（供 hosts）◄┘
```

- **八个子系统**：wire（接触面）/ mirror（会话镜像）/ presentation（呈现）/
  extensions（扩展 runtime）/ **settings（配置与数据）** / **packages（包索引与生命周期）** /
  **assets（数据资产：主题/键位）** / hosts（前端接入）；
- **bus 是脊柱**：后端事件经 wire 上线后一律进 bus，其余子系统都是订阅者——
  事件源架构，层间无直接调用；
- **扩展即组织原则**：内建的渲染器、部件、主题**与第三方扩展走同一个 API 注册**
  （dogfooding，VS Code 模式）——没有"内建专用通道"，API 的完备性由自己先验证；
- **slot 是统一抽象**：一切"内容贡献"都是向命名 slot 注册生产者
  （工具渲染、消息渲染、区域部件同一形态，见 §4）；
- **settings/packages 是基础设施而非扩展的附属**：前端、设置面板、包面板、
  扩展宿主共同消费——所以是独立模块（§7/§8），不寄生在 extensions/ 里。

### 2.1 TS 扩展点全集（本层扩展 API 的完整面）

| 域 | 扩展点 | 形态 | 状态 |
|---|---|---|---|
| 呈现 | `tool:*` / `entry:*` slot（渲染器） | slot 生产者（§4） | v3 已定 |
| 呈现 | `region:*`（footer/header/status/widget） | slot 生产者或逃生舱组件 | v3 已定 |
| 呈现 | **块词汇开放**：`block:<kind>` 新块类型 + 前端块渲染器 | slot 贡献（§4.2） | **v3.1 新增** |
| 行为 | 事件订阅（bus.on） | 观察式 | v3 已定 |
| 行为 | 命令（统一命令表）/ 快捷键（Node 执行） | 注册 | v3 已定 |
| ~~行为~~ | ~~工具（反向工具通道）~~ | **已取消（R12 撤销）**——工具永远 Python 侧执行 | v3.1 撤销 |
| **配置** | **settings：扩展声明用户设置键**（`api.settings.define`，schema 走代码） | §7 | **v3.1 新增** |
| **数据** | **state：扩展内部 KV 存储**（`api.state`，命名空间隔离） | §7 | **v3.1 新增** |
| 资产 | themes / keybindings（散装数据资产，三源发现） | §9 | v3.1 补全 |
| 包形态 | 复合包全规格（Python 6 类目 + `ui/` 段） | §8 | **v3.1 补全** |
| 产品面 | 包面板 = 内建扩展（dogfood 终极验证） | §8 | **v3.1 新增** |

---

## 3. wire：与后端的唯一接触面

```
wire/
├── client.ts           spawn/关停（command: string[]，后端可换语言）；
│                       请求-响应配对 / 并发分派 / 写锁 / stderr 直通
├── protocol.gen.ts     契约生成类型（Python 构建期导出，漂移测试保鲜）
├── capabilities.ts     契约版本（major/minor，✅ R6 已修：major 不等硬拒、
│                       minor 差放行）+ 能力位模型（domains/methods 驱动前端降级）
└── bridge.ts           反向通道路由：ui/request ↔ ui/response（原语）
```

纪律：wire 不懂呈现、不懂会话语义——帧进来翻译为 bus 事件，命令从门面转发。
后端的任何实现细节（Python/Rust/内部架构）不透出本层。

---

## 4. presentation：slot 模型 + 声明式块

### 4.1 slot：统一的内容贡献抽象

一切"某处需要一块 UI 内容"都是 slot。三类内建 slot 族：

| slot | 键 | 生产者 | 消费者 |
|---|---|---|---|
| 工具渲染 | `tool:<tool_name>` | 渲染器：`(card) → blocks[]` | 前端卡片组件 |
| 消息/条目渲染 | `entry:<role或custom_type>` | 渲染器：`(entry) → blocks[]` | 前端条目组件 |
| 区域部件 | `region:<footer·header·status·widget:*>` | 部件：`(state) → blocks[]` 或逃生舱组件 | 前端布局 |

- **注册表只有一个**：`SlotRegistry.register(slotKey, producer)`，带注册顺序与
  覆盖规则（后注册覆盖同键，记录来源用于诊断）；
- **通用回退是 slot 的空态**（无生产者时的 args+文本+状态色），不是"内建渲染器"；
- 内建渲染器（官方 bundle 的 bash/edit 等）与第三方**走同一 register 调用**——
  它们只是 `ui/renderers/` 加载器注册的普通生产者。

### 4.2 声明式块词汇（开放集）

内建五种：`diff / markdown / code / json / table`。块是**框架无关的呈现描述**：
生产者是纯函数（数据 → 块），前端把块适配为框架组件（pi-tui / DOM）。

**开放注册（v3.1）**：`block:<kind>` 也是一种 slot——扩展可注册**新块类型**
（如 `terminal-session`、`chart`），前端经同一注册表登记该块的渲染器
（每种前端各自实现）。内建块与扩展块同权；前端遇到未注册块类型时降级为
`json` 块展示（声明式的天然兜底）。

**逃生舱**：footer/header/editor/overlay 等深度定制，slot 允许注册
**前端特定组件工厂**（可选 tui/web 两套实现）——pi 的 space-invaders/doom 级
自由度从这里走，但默认轨永远是声明式。

### 4.3 区域状态模型

footer/status/widget 的内容是**状态**（会被更新），不是一次性渲染：
presentation 维护区域状态（key → 文本/块集），扩展经 API 更新，hosts 订阅变更。

---

## 5. bus：脊柱（观察式事件总线）

- **货源一**：后端原始事件（wire 注入，Bus 2 全 21 种 + 将来扩展）；
- **货源二**：派生事件（mirror/presentation 在状态迁移确定后发布：
  `mirror:synced`、`turn:started`、`tool:failed` 等便利词汇）；
- **观察式纪律**：handler 一律 `void`，fire-and-forget，异常隔离
  （拦截语义归 Python Bus 3，本层永不做结果合并）；
- **mirror 是特权订阅者**：按序、每条恰好一次（状态正确性）；
  其余订阅者无序、可错过（观察语义）；
- 前端输入事件（键位/命令）**不进 bus**——键位归前端，命令走门面。

---

## 6. extensions：扩展 runtime（pi ExtensionRunner 对位）

### 6.1 模块

```
extensions/
├── loader.ts       发现源 = resources/discovery.ts（§8）+ jiti 加载（写裸 .ts 免构建）
├── host.ts         生命周期：包装/卸/更新重载、错误隔离、trust 门控
└── api.ts          NovaUIExtensionAPI（§6.2）
```

### 6.2 扩展 API（与 pi 对位）

装载期 `ExtensionUIAPI`（注册）：registerRenderer/registerRegion(+Component)/
registerOverlay/registerBlock/registerEditor/registerCommand/registerShortcut/
registerEntryRenderer/registerAutocompleteProvider + settings/state。

运行期 `ExtensionUIContext`（pi ExtensionUIContext 对位——命令/快捷键 handler
的 ctx，全 UI 向）：invoke/invokeCancellable/notify/refreshPackages +
本地四件对话框（select/confirm/input/editor）+ **custom 模态对话框宿主**
（pi custom 对位——组件挂编辑器槽位或 overlay，done 交还结果）+
编辑器通道（getEditorText/setEditorText/pasteToEditor）+ writeClipboard +
setStatus（footer 扩展状态行）+ onTerminalInput + toolsExpanded 读写 +
主题访问器三件 + registerForegroundTask（Esc 域级路由登记）。

| pi 扩展 API | 本层 | 说明 |
|---|---|---|
| `on(event)` | `bus.on` | 观察式 |
| `registerMessageRenderer` / entry renderer | `slots.register("entry:*", fn)` | 统一 slot |
| tool renderCall/renderResult | `slots.register("tool:<name>", fn)` | 统一 slot |
| `setStatus/setWidget` | `ctx.setStatus` + `regions.set(key, blocks)` | 状态行 + 区域模型 |
| `setFooter/setHeader/setEditorComponent` | `slots.register("region:*", factory)` | 逃生舱 |
| overlay/focus 组件 | `ctx.custom(..., {overlay})` + OverlayHost | 模态/浮层双形态 |
| `select/confirm/input/editor/custom` | `ctx.select/confirm/input/editor/custom` | 本地对话框原语 |
| `setEditorText/getEditorText/pasteToEditor` | `ctx.*` 同名 | 编辑器通道 |
| `addAutocompleteProvider` | `api.registerAutocompleteProvider` | 组合进基线补全 |
| `onTerminalInput` | `ctx.onTerminalInput` | 键位路由前拦截 |
| theme get/set | `ctx.getTheme/getAllThemes/setTheme` | |
| `registerShortcut` | `api.registerShortcut` | handler 在 Node 执行（本地）；Python 扩展的走 `invokeShortcut`（后端执行） |
| `registerCommand` | `api.registerCommand` | 进统一命令表（§7） |
| ~~`registerTool`~~ | ❌ **已取消**——工具永远 Python 侧执行（R12 撤销） | |
| `registerFlag/registerProvider/spawn hook` | ❌ 归 Python 扩展 | |
| themes（JSON） | `themes/` 加载器 | 资产非代码 |

**内建 dogfood**：官方 bundle 的渲染器/部件经同一 `slots.register` 注册；
**/tree 已整体迁入 bundle frontend/tui/ 段**（组件 + 编排 + registerCommand——官方
命令 UI 与第三方同机制，宿主零内置命令 UI）。宿主 TUI 部件经
`nova-tui/modes/tui/*` 子路径导出共享（jiti 别名 + 原生 ESM 缓存，
主题/键位单例与宿主同实例——双包分裂实测排除）。

**两段式包结构（终局定稿）**：包 = `backend/`（Python 半区）+ `frontend/`
（TS 半区，自含 package.json/tests）；B 型纯前端包**根即前端半区**。
前端段按宿主分段（`frontend/tui/`，M3 增 `frontend/web/`），内部镜像后端
资源类型目录（`tools/`、`user_tools/`、`extensions/`——位置即语义）。
渲染器契约双形态：`NovaBlock[] | Component`（块可过网，组件全能力——
判别在消费点，官方三渲染器已组件化）。

### 6.3 加载与生命周期

- 发现：复合包 `ui/` 约定段（不进 `[tool.nova]`）；发现源统一为 packages/ 子系统（§8）；
- 入口：`<host>/index.ts` 默认导出 `(api) => void`（全量扩展）；
  `<host>/tools|user_tools/*.ts` 渲染器（文件名即 tool_name；返回块或组件）；
- 生命周期：包装/卸/更新重载；单扩展异常隔离（不碍宿主与他人）；
- trust：不被信任项目的 frontend/ 段不加载。

### 6.4 ~~反向工具通道~~（R12 撤销，v3.1 后期判决）

**判决：工具永远在 Python 侧执行，Node 不执行任何后端能力。** 原"反向
工具通道"（registerTool：定义在后端、`tool/invoke` 回 TS 执行）取消：

- 分工终版：Node = 呈现推导 + 前端本地行为；工具执行 = Python（唯一实现）；
- 交互式工具（执行中询问用户）的出路：Python 工具经**反向原语**
  （`ui/request`：select/confirm/input）——permission_gate 已验证该链路，
  无需 Node 执行即可齐平 pi 的交互式工具；
- 牺牲的远期场景：M3 远程后"必须在前端机器执行的工具"（操作浏览器/编辑器
  环境）——真有需求再议，现在不背。

---

## 7. settings：配置与数据子系统（独立模块）

pi 在这件事上只有 flags + `appendEntry` + 自写文件——本层把它做成一等公民
（VS Code 的 configuration contribution 是参照系，但 schema 走代码不走元数据文件）。
消费者：扩展（`api.settings`/`api.state`）、设置面板（内建扩展）、前端。

```
settings/
└── store.ts        UISettings（设置键注册表 + ui-settings.json 原子写 +
                    变更回调）+ UIStateStore（ui-state/<扩展>.json 每扩展
                    一个文件，原子写）——registry/watch 内聚其中
```

**settings（用户可见配置）**：

- 扩展经 `api.settings.define("myext.interval", { type: "number", default: 30, description: "…" })`
  声明设置键（schema 在代码里，与"工具即代码"同一纪律）；
- **存储归 Node 层**（`~/.nova/agent/ui-settings.json`）：后端 settings 不背——
  它的 schema 有未知键拒绝，且这是前端域；两个文件主权分明（后端管运行时，
  Node 管 UI/扩展）；
- 读写：`api.settings.get(...)`（自动并入默认值与类型）；用户面经设置面板
  （内建扩展 dogfood）编辑，扩展设置有专属区。

**state（扩展内部数据）**：

- todo 的条目、bookmark 的列表这类**数据不是用户设置**，是扩展内部状态；
- `api.state.get/set(key, value)`——按扩展命名空间隔离的 KV；
- 与 settings 的边界一句话：**用户会想在设置面板里调的 → settings；
  扩展自己记来干活的 → state**。

---

## 8. packages：包索引与生命周期（独立模块）

后端 `nova-pkg` 管包的**物理生命周期**（装/卸/更新）；本子系统是本层对包世界的
**索引与感知**——扩展宿主靠它发现 `ui/` 资产并驱动重载，包面板靠它出数据。

```
packages/
├── registry.ts     已安装包索引（pkgList RPC 拉取 + 缓存 + 失效策略）
├── assets.ts       ui/ 资产索引：pkg → {index.ts?, renderers/*.ts, themes/*.json}
│                   （resources/loader 的唯一发现源）
├── lifecycle.ts    装/卸/更新监听（RPC 后失效重扫）→ 通知 extensions host
│                   加载/卸载/重载对应扩展
└── updates.ts      pkgCheckUpdates 结果缓存（启动更新提醒与包面板角标的数据源）
```

**复合包全规格**（一个包能贡献的一切）：

```
my-package/
├── pyproject.toml [tool.nova]    → Python 身份 + pip 依赖 + 能力 6 类目：
│                                    agents / tools / skills / extensions /
│                                    prompts / user_tools
├── package.json                  → 【根】npm 清单（dependencies；devDependencies 不装；
│                                    仅 TS 侧有第三方依赖时才需要；version 被忽略——
│                                    版本权威在 pyproject）
├── package-lock.json             → 【根】锁版本（有则 npm ci，无则 npm install）
├── node_modules/                 → 【根】npm 安装产物（每包自含，天然隔离；不入库）
├── tsconfig.json                 → 【根】TS 开发配置（dev-only）
├── src/<pkg>/                    → Python 共享 helper（有 build-system 时自安装）
├── agents/ tools/ skills/ extensions/ prompts/ user_tools/
└── ui/（约定段，纯源码与资产）    → TS 资产：
    ├── index.ts                  → 扩展入口（工厂，全量 API：slot/region/命令/
    │                               快捷键/settings/state/通知）
    ├── renderers/<tool>.ts       → 纯函数渲染器（轻量入口，仅渲染；文件名 = 工具名）
    ├── lib/                      → TS 共享代码（渲染器/扩展共用）
    ├── components/               → 逃生舱组件（被 index.ts import）
    └── themes/*.json             → 主题资产
```

- **package.json 永远在包根**（A/B 型一条规则；JupyterLab 扩展同款 polyglot 形态）；
  ui/ 是纯源码与资产目录（零清单零产物）；
- settings 键 / 块类型 / 部件 / 命令 / 快捷键 / 工具**全部在代码里声明**，
  `ui/` 下不加任何元数据文件（与"工具即代码"同一纪律）；
- 一次安装、一个版本号、一个 trust 决策（nova-pkg 统一物理生命周期）。

### 8.1 复合依赖编排（pip + npm 两个生态系统）

**原则**：nova-pkg 不"管理"任何环境，只**编排子进程**——git/pip/uv/curl 之后，
npm 是第四个被编排的。每个生态系统用自己的 manifest（`pyproject.toml` ↔ 根
`package.json`），互不寄生；**没有全局 node 环境要管**——`node_modules`
每包自含（在包根），冲突天然不存在。

**nova-pkg install 五阶段**：

```
① 物化（copytree/软链，整目录含 ui/）
② pip/uv 依赖（pyproject）
③ 自安装（--no-deps 装工具包本体）
④ 二进制依赖
⑤ npm 阶段：包根 package.json 存在 → npm ci --omit=dev（有 lock）
     或 npm install --omit=dev（无 lock）；
     copy 模式在安装副本根执行，editable 模式在源目录根执行
     （与 pip -e 语义一致：依赖安装、源码链接）
```

- **npm/node 可用性**：`binary_system_dependencies` 同款——存在性检查 +
  缺失警告（附安装指引），不代装 node 本体（跑 TS runtime 的机器本就有 node）；
- **NOVA_OFFLINE**：跳过 npm 阶段 + 警告（与二进制下载同一离线语义）；
- **失败解耦**：npm 阶段失败 = 该包 TS 资产不可用（诊断），能力部分不受影响；
- **Node 层自愈**：`packages/lifecycle.ts` 加载前发现 `node_modules` 缺失
  （离线安装/npm 当时不在/手动复制）→ 自跑 npm install——nova-pkg 主装、
  Node 层自愈，两个入口一个真相（目录现状）；
- **jiti 解析**：Node 标准向上解析——`ui/*.ts` 的 import 自然命中包根
  `node_modules`，无需特殊解析根配置。

**包面板（dogfood 终极验证）**：应用内包管理面板（VS Code Extensions 面板形态——
列表/详情/装/卸/更新/更新角标）**本身写成一个内建扩展**：消费统一命令表 +
RPC package 域 + packages/ 索引 + region/slot。扩展 API 强到能长出我们自己的
包面板，才算完备（R1 的验收标准）。

---

## 9. assets：数据资产子系统（主题/键位）

**收窄的"resource"**：不是 Python ResourceLoader 的翻版——代码资产
（`ui/*.ts`）已由 resources/discovery + resources/loader 覆盖且**只来自包**；
本子系统只管**可散装的数据资产**（用户调参性质，无执行性，不强制打包）。

```
assets/
├── themes/
│   ├── builtin.ts      内建主题（极少，2-3 个兜底，dogfood 同一注册通道）
│   └── loader.ts       三源发现 + 注册 + 优先级裁决：
│                       ① 包内（resources/discovery.ts 索引，随包分发）
│                       ② 用户级 ~/.nova/agent/ui/themes/
│                       ③ 项目级 <cwd>/.nova/ui/themes/
└── keybindings.ts      键位 JSON（②③ 同型；内置键位表在 modes/tui 前端）
```

- **来源追踪与优先级**：`project > user > package`（同名项目级覆盖，诊断记录来源——
  对齐 Python `SourceInfo` 先例）；
- **激活主题是设置不是资产**：`settings/ui-settings.json`（§7）记录当前主题名；
- **无执行性故无 trust 门控**（颜色/键位 JSON 不跑代码；代码资产才走 trust，
  与 resources/loader 的纪律互补）；
- **不进后端**：Python 完全不知道这些文件存在（themes 当年移出 Python 资源系统
  正是为此）；前端经注册表选用，块渲染器/组件消费当前主题。

---

## 10. 统一命令表

前端可调的命令三源合并：**后端 RPC 方法**（61 域方法）+ **后端扩展命令**
（`getCommands` 透出，prompt 转发）+ **Node 扩展命令**（§6.2 注册）。
palette 数据源统一，键位/slash 共享同一表——pi 的命令面在本层的对应形态。

---

## 11. hosts：前端接入层

```
hosts/（M3 再立目录；当前 RuntimeHost 接口与进程内实现同居 runtime.ts——
        index.ts 公共面不变，届时内部搬迁零成本）
├── inprocess.ts    进程内宿主：modes/tui 经 RuntimeHost API 消费
└── websocket.ts    【M3】远程宿主：WS **客户端**（WS 服务器/扇出已归 Python
                    core/rpc，2026-08 翻案——见 nova_architecture_2.0.md 文首修订）
```

`RuntimeHost` 是前端面对的唯一接口：

- `subscribe(model)`：渲染模型（mirror 投影 + slot 产出 + 区域状态）变更订阅；
- `invoke(command, params)`：统一命令表调用；
- `onUIRequest/sendUIResponse`：反向原语转交；
- 客户端可以任何语言——**类型经 `nova-wire.schema.json` codegen**（语言中立），
  归约逻辑由本层集中（schema 生成不了状态机）。

---

## 12. mirror：会话镜像（已实现，bus 脊柱化完成）

```
mirror/
├── mapping.ts    纯函数归约器（与 pi interactive-mode 状态机同构；
│                 ✅ R7 已补：message_end(aborted/error) 收尾未完结卡片，
│                 agent_end 兜底）
├── store.ts      sync（快照+条目全量）+ apply（事件增量）+
│                 ✅ 四事件增量直写（model_changed 等 payload 即事实）+ addNotice
└── types.ts      呈现自有词汇 + SessionSnapshot（✅ R8 已修：re-export 生成类型）
```

mirror 经 bus 特权订阅接收事件（按序、恰好一次、先于观察者），并发布派生
便利事件（``session:synced`` / ``turn:started`` / ``turn:ended``）。

---

## 13. modes/tui（pi-tui 薄壳；原独立 nova-tui 包已并入本包 src/modes/tui/）

原则：薄壳（推导在 runtime）、pi-tui 只用在这里、不移植 pi 6000 行。

```
src/modes/tui/
├── main.ts          CLI 入口（commander，bin 指向编译产物）
├── app.ts           NovaTuiApp 薄壳：槽位布局、生命周期、装配 controllers
├── controllers/     编排层：editor/keymap/dialogs/transcript/status/theme/
│                    settings（16 项面板）/export/share/foreground（前台任务
│                    取消登记处）/terminal（OSC 0+9;4）/startup（启动编排）+
│                    前端自持导航四件套 tree（折叠/标签/摘要流/五模式过滤/搜索）/
│                    sessions（删除/重命名/作用域/排序/搜索语法）/fork（消息
│                    分叉回填）/models（/model ✓置顶+Tab 作用域 + scoped 池面板）
├── components/      transcript/（消息与工具卡片）、dialogs/（auth 等待 +
│                    form 表单）、pickers/（searchable 通用选择器 + tree +
│                    sessions 专用选择器）、status/（footer/loader）、
│                    layout/（welcome/resources/RegionHost/OverlayHost）
├── blocks/          声明式块 → pi-tui 组件适配（slot 注册制）
├── builtin/         内建扩展（/packages 包面板——dogfood）
├── themes/          主题三源（builtin > 用户目录 > 包 ui/themes）+ automatic
│                    跟随终端 + watcher 热更新 + 移动即预览
├── keymap/          三级键位合并 + 扩展快捷键对账
└── utils/           clipboard / terminal-guard（tmux 检测）/ signals（信号
                     与崩溃守卫）/ tui-settings（前端设置存储）/ changelog
```

包自持选择器的判决（/tree、/fork、/resume、/model、/scoped-models、/todos
——已整体迁入官方 bundle `frontend/tui/extensions/session_commands/slash/*`）：
凡需要 **per-item 动作键**（折叠/删除/重命名）或**反向原语表达不了的交互**
（Tab 作用域/勾选排序面板）的选择器，归**包的 frontend 段**以
ExtensionUIAPI 自持（registerCommand + ctx.custom 模态 + ctx.invoke 直调
RPC——官方与第三方同机制，dogfood）；四件套反向原语只承载决断/选择/文本/
告知原子，包自定义对话框经 `dialog:*` slot 注册（question 为首个实例）。
宿主只保留 runCommand 命令通道与本地小件（/theme /settings 等），后端同名
命令保留作 headless 回退。双 Esc 导航经 `runCommand('tree'|'fork')` 推命令。

`nova pkg` 子命令保留现状（spawn `nova-pkg --json` 薄壳）。

---

## 14. 类型分层

| 类别 | 来源 |
|---|---|
| 契约类型（事件/条目/方法形状/快照） | **构建期生成**（gen.ts），手写即双源 |
| 呈现自有词汇（卡片/条目/状态） | 手写（Node 层合法概念） |
| 声明式块（§4.2） | **本层定义**（扩展↔前端的契约，不进 Python） |
| 自由负载（details 等） | `unknown`，工具作者的事 |

---

## 15. 判决记录

| # | 判决 | 理由 |
|---|---|---|
| R0 | 终态 = pi 同等能力 TS runtime（智能体 RPC 可插拔），启动在 TS | 总目标（不变） |
| R1 | **扩展即组织原则：内建与第三方同一 API（dogfood）** | v3 核心；API 完备性自己先验证，无"内建专用通道" |
| R2 | **slot 统一内容贡献抽象**（tool/entry/region 三族一表） | v3 核心；渲染器/部件不再是三个系统 |
| R3 | **bus 为脊柱，mirror 为特权订阅者** | 事件源架构；层间无直接调用 |
| R4 | mapping 在本层，输出框架无关模型 | 呈现逻辑写一次；样式分叉推给 slot 消费端 |
| R5 | 声明式块默认轨 + 前端特定组件逃生舱 | 不焊死框架；pi 自由度有逃生门 |
| R6 | 契约版本 major/minor（✅ 已修） | major 不等才硬拒；minor 靠能力位降级 |
| R7 | abort 未完结卡片标 error（✅ 已补） | pi message_end 收尾模式 |
| R8 | SessionSnapshot = re-export 生成类型（✅ 已修） | 消灭手写双源 |
| R9 | fork/label/tree = 选择器模式 | pi 验证；live 合成 id 不进 RPC |
| R10 | 渲染器/扩展只来自包 `ui/` 段，零内置 | 零内置教义；通用回退是空态不是内建 |
| R11 | bus 观察式（void handler） | 拦截归 Python Bus 3 |
| R12 | ~~反向工具通道~~ **已撤销**——工具永远 Python 侧执行；交互式工具走反向原语 | Node 不执行后端能力（分工终版，§6.4） |
| R13 | 统一命令表（后端方法 + 后端扩展命令 + Node 命令） | pi 命令面的本层形态 |
| R14 | TUI 为薄壳（modes/tui），pi-tui 只进它 | 框架无关原则 |
| R15 | **扩展 settings（Node 存储）与 state（KV 隔离）是一等扩展点** | pi 只有 flags/appendEntry/自写文件，太薄；配置与数据分家（§7） |
| R16 | **块词汇开放注册**（`block:<kind>` slot，未注册块降级 json） | 声明式词汇不能被内建五种封死（§4.2） |
| R17 | **包面板写成内建扩展（dogfood 验收）** | 扩展 API 完备性的终极证明（§8） |
| R18 | **settings/packages 提升为独立子系统（§7/§8）** | 基础设施（前端/面板/宿主共消费），不寄生 extensions/ |
| R19 | **assets = 收窄的数据资产子系统（§9），非 Python ResourceLoader 翻版** | 代码资产只来自包（已有家）；数据资产（主题/键位）允许散装，三源 + project>user>package |
| R20 | **复合依赖编排：各生态系统用自己的 manifest（pyproject ↔ 包根 package.json），nova-pkg 加 npm 阶段，Node 层自愈兜底** | Python 不"管理"node 环境，npm 只是第四个被编排的子进程；node_modules 在包根每包自含（§8.1，已实现） |

---

## 16. 落地顺序

1. ~~**M2 前小修**：R6 + R7 + R8~~（✅ 已完成——与骨架重写合并为一次成型，
   不再缝补）；
2. **M1 薄 TUI**（§13）：端到端打通，第一个真实前端；
3. **骨架升级（原 M2）**：~~bus 脊柱化~~（✅）+ ~~slot/presentation 层~~（✅）+
   ~~渲染器加载器（`ui/renderers/*.ts` 纯函数级，经 slot 注册——dogfood 已跑通：
   官方 bundle 与 B 型包的 bash/edit/read 渲染器真实加载并产出块）~~ +
   薄块适配器（归 modes/tui）+ **settings/state 子系统**（§7，随 M4 扩展
   API 一同落地——没有消费者的存储层是投机）；
4. **M3**：WS 宿主 + 远程信任门控；
5. **M4（扩展 runtime 全量）**：host/loader/api 全量（§6）+ 区域逃生舱 +
   theme 加载 + settings/state + **包面板内建扩展**——本层能力
   对齐并超越 pi 的终点。
   **配套（nova-pkg 侧）**：install 第 ⑤ 阶段（npm 编排，§8.1）✅ 已实现——
   包根 package.json 检测 → npm ci/install，失败解耦（`test_npm_stage.py` 锁定）。

**重写说明（v3.1 落地）**：初稿代码直接按目标骨架一次重写成型（wire/bus/
mirror/presentation/packages/extensions 六子系统目录就位），未走"先小修再升级"
的两步——重写时 R6/R7/R8 作为设计决策直接落进新结构，而非对旧代码的补丁。

**注意 v3 与 v2 的路线差异**：不再"先注册表后宿主（两级）"——slot 注册表第一天
就在扩展 API 内（同一 register 调用），宿主骨架与注册表同生，API 面随里程碑长大，
无返工（"拓展不是重写"的结构性保证）。

## 17. 挂账

- 内联动作的 entry id 通道（turn 末对账 vs `entry_appended`，有内联 UX 再议）；
- 子进程监督重启 / 断线 resync（M3 再评）；
- `sync()` 的 compaction/branch_summary/custom 条目渲染（归 slot `entry:*`）；
- 一致性套件的 LLM 事件覆盖（假模型通道）；
- M3 远程包写操作的信任门控；
- ~~**npm registry 作为包源类型**~~（✅ 已落地——`npm:name[@version]` 精确版/latest +
  全量 range 语法（`^`/`~`/部分版本/`*`/通配段/比较器集/`||`/hyphen range，
  自研 `_semver.py` max satisfying）；残余：dist-tag 引用（`npm:pkg@beta`）明确报错）；
- 前端自持选择器的残余打磨（pi 对位长尾，**已清零**）：
  ~~tree 的 filter 五模式/搜索/水平视口~~（✅ ctrl+d/t/u/l/a 直切 + ctrl+o 循环 +
  token AND 搜索 + 选中行锚点左移）；~~session 的排序/命名/路径/搜索语法~~
  （✅ ctrl+s threaded/recent/relevance + ctrl+n/ctrl+p + re:/引号/fuzzy +
  threaded 树形预览）；残余仅 tree 的 ctrl+x 复制与标签时间戳切换；
- ~~分支摘要的 Esc abort 接线~~（✅ ForegroundTasks 前台任务登记处——Esc 域级
  路由一环：本地 AbortError + cancelRequest 上行 + abortBranchSummary 域级
  RPC（新增，契约 MINOR 3）三重取消）；
- ~~`/share` 无 Esc 取消加载框~~（✅ 同一 ForegroundTasks 通道——kill gist
  子进程 + 静默收尾）；
- `dialog:*` slot（后端声明的自定义原语词汇的前端组件注册——与已落地的
  `ctx.custom`（Node 扩展的模态宿主原语）分工：前者服务后端自定义词汇，
  后者服务 Node 扩展命令 UI；form 已原生落地）；
