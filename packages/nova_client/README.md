# nova-client

**Node 扩展运行时**——Nova 三层架构（设计见 `packages/nova_harness/examples/nova_architecture_2.0.md`）的第二层：
TUI / Web UI 等前端共享的厚应用层。TUI 作为本包内的一种宿主形态同居（`src/modes/tui/`，`bin.nova` 入口）。

> 文档四件套：[`docs/design.md`](docs/design.md)（为什么这么设计：判决记录）；
> [`docs/frontend-backend-separation.md`](docs/frontend-backend-separation.md)（前后端分治与路径约定）；
> [`docs/tui-design.md`](docs/tui-design.md)（TUI 宿主设计）；[`docs/ui-primitives.md`](docs/ui-primitives.md)（UI 标准原语词汇）。

```
前端（TUI / Web UI）     纯渲染 + 键盘/鼠标        ← 本包的消费方
      ↕ RuntimeHost API（进程内；Web/桌面端落地时可 WebSocket）
nova-client          呈现模型、事件脊柱、渲染器注册表、后端会话管理
      ↕ JSON-RPC over stdio/WS（哑管道：{type, data} 直通）
nova_server（Python）   协议服务层（归约/item 词汇在此），下接 nova_harness 纯 agent 运行时
```

## 结构（按现行 src 目录）

| 目录/文件 | 职责 |
|---|---|
| `wire/` | 与后端的唯一接触面：`client`（spawn/生命周期/请求-响应配对）、`capabilities`（契约 major/minor 握手 + 能力位）、`bridge`（反向原语路由：ui/request ↔ ui/response） |
| `bus.ts` | 观察式事件脊柱（单文件）：后端事件一律上线；mirror 特权订阅（按序恰好一次、异常响亮冒泡），观察者异常隔离 |
| `mirror/` | 会话镜像：`store`（item 清单 + 快照/事件水位同步 + 增量应用 + `deriveEntries` 视图派生）、`mapping`（`applyDelta` 纯函数——线上键解析与白名单追加，前后端同一语义）、`types`（呈现词汇）。**归约已归服务器**（`nova_server/reduction/`），本层不再做事件→呈现归约 |
| `presentation/` | `blocks`（声明式块词汇 + validateBlock schema 校验）+ `slots`（tool/entry/region/block/editor/command/shortcut/autocomplete/dialog 九族键注册表）+ `extension-api`（扩展 UI API——ctx 只收"后端够不着的宿主原语"）+ `theme-json`（主题契约） |
| `packages/` | 已安装包索引（pkgList）+ npm 自愈（node_modules 缺失补装）+ 更新提醒 |
| `resources/` | 呈现资源层：`discovery` 统一发现（包内 `frontend/` 段镜像约定 + 散养根 `~/.nova/agent/frontend/tui/` 与 `<cwd>/.nova/frontend/tui/`）+ `trust` 编排过滤（user 恒可信、project 过 trust 门）+ `loader`（jiti 管线：mtime 缓存/preview 钩子/diagnostics） |
| `settings/` | UISettings 扩展设置 + UIStateStore 扩展 KV（Node 层存储，不进后端 settings） |
| `keymap/` | 键位能力子系统：keybindings.json 加载 + 三级合并 + 保留键位对账（TUI 默认键位表在 `modes/tui/keymap/tables.ts`） |
| `export/` | 会话导出 HTML（模板三件套 + vendored marked/highlight.js，宿主无关、主题注入） |
| `runtime.ts` | `RuntimeHost` 接口与进程内实现（`NovaUIRuntime` facade）同居 |
| `modes/tui/` | TUI 宿主形态：app 装配根 + controllers 编排层 + components（transcript/dialogs/pickers/status/layout）+ blocks 适配 + themes + `bin.nova` 入口 |
| `migration.ts` / `paths.ts` | 前端旧位启动自动迁移 / 前端域路径约定 |

类型管道：线上契约由 Python 侧构建期导出（`nova_server.protocol.schema_export`）——
`protocol/nova-wire.schema.json` + `src/protocol/nova-wire.gen.ts`，mapping/store/wire 全部基于生成类型编程；
Python 改事件 → 重新导出 → TS 编译显形漂移（pytest 侧有漂移测试）。

## 不在本包（前端的事）

渲染、键盘、主题、布局——TUI（pi-tui 组件）与将来的 Web UI 各自实现；
本包只给"可渲染模型"。

## 里程碑

- ~~M1~~：RPC client + 事件管线 + 会话存储 + 进程内 API + 构建期类型管道；
- ~~M1 薄 TUI~~：原独立 `nova-tui` 包已并入本包 `src/modes/tui/`（TUI = 运行时的一种宿主形态，`bin.nova` 入口）；
- ~~归约归服务器~~：mirror 退化为 apply 容器（item 清单 + 视图派生），渲染器契约换线上 item；
- **M3（部分）**：WebSocket 传输已归 Python 侧承载（`nova_server` 连接化后 stdio/WS 同为连接来源）；nova_client 侧 WS 宿主随 Web/桌面端落地；
- **M4（部分）**：settings/state 子系统、`dialog:*` slot、`/packages` 包面板、keymap 子系统已建成；全量 UI 扩展宿主挂账（见 `docs/design.md` §16）。

## 用法

```ts
import { NovaUIRuntime } from 'nova-client';

const runtime = new NovaUIRuntime({ session: { cwd: process.cwd() } });
runtime.subscribe((change) => { /* change.area: transcript | status | snapshot | queue */ });
runtime.onUIRequest((req) => { /* 弹对话框，然后 runtime.sendUIResponse(req.id, answer) */ });
await runtime.start();
await runtime.prompt('hello');

// 工具渲染器（已安装包 frontend/tui/tools/<tool>.ts 自动发现加载）
const render = runtime.slots.resolveToolRenderer('bash');
const blocks = render?.({ item }); // → NovaBlock[] | Component（前端各自适配）
```

## 构建与测试

```bash
npm install
npm run build   # tsc → dist/
npm test        # tsx --test（mapping/store/bus/capabilities/slots/loader 等单测，tests/ 镜像 src 子路径）
npm run tui     # tsx 直接运行 TUI（src/modes/tui/main.ts）
```
