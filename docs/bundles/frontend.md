# 前端渲染器（TUI 呈现半区）

包的后端能力（Python）与呈现（TypeScript）分半区。TUI 前端**独立进程**发现包内 `frontend/tui/` 资产并经 jiti 加载——位置即语义：

```
frontend/tui/
├── tools/<tool>.ts       # 工具卡片渲染器（文件名即渲染的工具名）
├── dialogs/<name>.ts     # 自定义对话框（注册 dialog:<name> slot）
├── index.ts              # 前端扩展入口（ExtensionUIAPI 工厂，可选）
├── themes/*.json         # 主题（theme-json 契约，可选）
└── lib/                  # 辅助模块（不进发现面）
```

## 工具渲染器契约

```ts
// frontend/tui/tools/my-tool.ts
import { Container, Text, type Component } from '@earendil-works/pi-tui';
import { detailsOf, type RendererInput } from 'nova-tui';

export default function renderMyTool(input: RendererInput): Component {
  const d = detailsOf(input);              // = 后端 AgentToolResult.details
  const colors = input.env?.colors;        // 主题色函数（dim/accent/success/error/...）
  const expanded = input.env?.expanded === true;  // ctrl+o 展开态
  // 组装 pi-tui 组件树
  const c = new Container();
  c.addChild(new Text(`结果：${String(d.value ?? '')}`, 1, 0));
  return c;
}
```

**契约要点**：

- **输入即线上归约成品**：`input.item` 是服务器归约的 `ToolCallItem`（无中间卡片模型）；`detailsOf(input)` 取后端 `details`；
- **双形态返回**：`NovaBlock[] | Component`——声明式块（简单场景）或活 pi-tui 组件（推荐，官方渲染器全走组件）；
- **`input.status`**：`running` / `done` / `error`——运行态样式据此切换；
- **`input.env`**：主题色、展开态、终端尺寸等宿主环境；
- **可选命名导出 `preview`**：执行前只读预览（plan 模式/确认前展示将做什么）。

### 渲染器纪律（宿主约定，写前必读）

- **运行态不自渲**：`running` 文本与执行计时归宿主统一（ElapsedLine）——渲染器只表达"在跑什么"（bash 的命令、grep 的模式），不输出 "Running…" 字样，否则与宿主双行叠加；
- 例外：等用户输入的语义文本（如 question 的 `waiting for answer…`）是内容不是计时，可以渲；
- 颜色只经 `input.env.colors` 取（主题自适应），不硬编码 ANSI；
- 折叠/展开两态都要有意义：折叠态给摘要（进度行/前几行），展开态给全量。

## 自定义对话框（`dialog:<name>` slot）

后端工具/扩展经 `ctx.ui.request("dialog:my-dialog", {...})` 触发；前端包注册同名 slot 接管渲染：

```ts
// frontend/tui/dialogs/my-dialog.ts
import type { DialogFactory } from 'nova-tui';

export const myDialogFactory: DialogFactory = (opts) => ({
  // 返回 pi-tui 组件 + 结果提交回调（看官方 dialogs/question.ts 样板）
});
export default myDialogFactory;
```

**能力协商闭环**：注册即触发前端 `system/capabilities` 重宣告 → 后端 `ctx.ui.has_capability("dialog:my-dialog")` 变真 → 走自定义对话框；未注册（headless/其他前端）时后端自动降级基线原语。**工具逻辑不出 Python，弹窗逻辑不出 TS**。

## 前端扩展入口（`index.ts`）

后端扩展管逻辑，前端入口管呈现层的命令 UI：

```ts
// frontend/tui/index.ts
import type { ExtensionUIAPI } from 'nova-tui';

export default function extension(api: ExtensionUIAPI): void {
  api.registerCommand('weather', {
    description: '查天气（弹城市选择器）',
    handler: async (args, ctx) => { /* ctx.custom 挂模态 / ctx.invoke 调后端方法 */ },
  });
}
```

`ExtensionUIAPI` 的面：slot 注册（`tool:`/`dialog:`/`entry:`/`region:` 等九族）、`registerCommand`、`registerShortcut`、`registerEntryRenderer`、`events.on`（事件观察口）、`invoke`（全量后端方法表，类型化）。**ctx 纪律：只收"后端够不着的宿主原语"**（对话框、编辑器、剪贴板、setStatus、终端让位……）；数据/动作一律 `invoke` 后端方法，不在前端另造状态。

## 宿主共享件

通用选择器件（searchable/selector/hints）经 `nova-tui/modes/tui/*` 子路径 import——**共享宿主单例，不复制实现**：

```ts
import { SearchableSelector } from 'nova-tui/modes/tui/components/pickers/searchable.js';
```

## 依赖与加载机制

- `frontend/package.json` 声明运行时 `dependencies`（如 `pretty-ms`）——安装期 `npm ci` 装配；缺失时 TUI **后台自愈**补装（不阻塞启动，补完刷新上线）；
- 加载器是 jiti（直读 TS 源，不需要构建步骤）；`*.test.ts` 不进发现面；
- 渲染器对 `nova-tui` 与 `@earendil-works/pi-tui` 的 import 经 virtualModules 直供（打包二进制形态下磁盘上无宿主 node_modules 也能解析）。

## 主题

`themes/<name>.json`（theme-json 契约：语义色键表）。三源：内建 dark/light ← 包分发 ← 用户目录。`/theme` 实时预览。

## 调试

- TUI `/debug` 导出加载诊断（发现路径/耗时/碰撞/失败原因全记录）；
- 渲染器加载失败按诊断降级为通用卡片，不炸会话；
- 测试放 `frontend/tests/`（node:test + tsx，镜像 `tui/` 子路径）。

下一页：[Agent 组合声明与人格](agents.md)。
