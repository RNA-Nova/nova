# nova-client

**Node 扩展运行时**——Nova 三层架构（`nova_architecture_2.0.md`）的第二层：
TUI / Web UI 等前端共享的厚应用层。

> 文档四件套：**[`docs/dataflow.md`](docs/dataflow.md)**（扇入/扇出/对应关系表，
> 最快的全景）；[`docs/architecture.md`](docs/architecture.md)（代码向导：
> 每条数据流怎么跑，深入读这份）；[`docs/design.md`](docs/design.md)（为什么这么
> 设计：判决记录）；[`docs/roadmap.md`](docs/roadmap.md)（落地顺序与当前进度）。

```
前端（TUI / Web UI）     纯渲染 + 键盘/鼠标        ← 本包的消费方
      ↕ RuntimeHost API（进程内；M3 起可 WebSocket）
nova-client          呈现模型、事件脊柱、渲染器注册表、后端会话管理
      ↕ JSON-RPC over stdio（哑管道：{type, data} 直通）
nova_harness（Python）   纯 agent 运行时，零 UI 概念
```

## 结构（六子系统，v3.1 骨架一次成型）

| 目录 | 职责 |
|---|---|
| `wire/` | 与后端的唯一接触面：`client`（spawn/生命周期/请求-响应配对）、`capabilities`（契约 major/minor 握手 + 能力位）、`bridge`（反向原语路由：ui/request ↔ ui/response） |
| `bus.ts` | 观察式事件脊柱（单文件）：后端事件一律上线；mirror 特权订阅（按序恰好一次、异常响亮冒泡），观察者异常隔离；派生便利事件（`session:synced`/`turn:*`） |
| `mirror/` | 会话镜像：`mapping` 纯函数归约器（事件 → transcript/status，可独立测试）、`store`（快照+历史全量 sync、事件增量、四事件直写）、`types`（呈现词汇，快照 re-export 生成类型） |
| `presentation/` | `blocks`（声明式块词汇 v1：diff/markdown/code/json/table）+ `slots`（tool/entry/region 三族统一注册表；空态 = 通用回退） |
| `packages/` | 已安装包索引（pkgList）+ `ui/` 资产发现 + npm 自愈（node_modules 缺失补装） |
| `extensions/` | 渲染器加载器（jiti 加载 `ui/renderers/*.ts` → slot 注册；project trust 门控）；全量扩展宿主归 M4 |

类型管道：线上契约由 Python 侧构建期导出（`nova_harness.core.rpc.protocol.schema_export`）——
`protocol/nova-wire.schema.json` + `src/protocol/nova-wire.gen.ts`，全部基于生成类型编程；
Python 改事件 → 重新导出 → TS 编译显形漂移（pytest 侧有漂移测试）。

## 不在本包（前端的事）

渲染、键盘、主题、布局——TUI（pi-tui 组件）与 Web UI 各自实现；
本包只给"可渲染模型"。

## 里程碑

- ~~M1~~：RPC client + 事件管线 + 会话存储 + 进程内 API + 构建期类型管道；
- ~~骨架~~：bus 脊柱 + slot 注册表 + 渲染器加载器（已对真实 dogfood 包跑通）；
- ~~M1 薄 TUI~~：原独立 `nova-tui` 包已并入本包 `src/modes/tui/`（TUI = 运行时的一种宿主形态，`bin.nova` 入口）；
- **M3**：WebSocket 接入 + 多客户端事件扇出（Web UI 开门）；
- **M4**：UI 扩展宿主全量（`ui/index.ts` 入口 + 扩展 API + settings/state + 反向工具通道）。

## 用法

```ts
import { NovaUIRuntime } from 'nova-client';

const runtime = new NovaUIRuntime({ session: { cwd: process.cwd() } });
runtime.subscribe((change) => { /* change.area: transcript | status | snapshot | queue */ });
runtime.onUIRequest((req) => { /* 弹对话框，然后 runtime.sendUIResponse(req.id, answer) */ });
await runtime.start();
await runtime.prompt('hello');

// 工具渲染器（已安装包 ui/renderers/ 自动加载）
const render = runtime.slots.resolveToolRenderer('bash');
const blocks = render?.(card); // → NovaBlock[]（前端各自适配为组件）
```

## 构建与测试

```bash
npm install
npm run build   # tsc → dist/
npm test        # tsx --test（mapping/store/bus/capabilities/slots/assets/loader 单测）
```
