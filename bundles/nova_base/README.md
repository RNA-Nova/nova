# nova-base

Nova 基础 bundle：会话产品基础设施。任何 Nova agent 产品的可用性底座——slash 命令、交互询问、任务清单、工具面板、破坏性操作确认，以及 UI 标准原语的官方定义。

本包不提供任何执行能力（不读文件、不跑命令）；编程能力由 [`nova-coding-agent`](../nova_coding_agent) 提供（其 `requires` 声明依赖本包）。

## 内容

### 扩展（`backend/extensions/`，3 个）

| 扩展 | 说明 |
|------|------|
| `session_commands` | 21 个会话 slash 命令：/help、/login、/logout、/model、/scoped-models、/compact、/tree、/fork、/resume、/new、/name、/session、/clone、/reload、/export、/import、/agent、/persona、/trust、/todos 等 |
| `tools_panel` | /tools 工具开关面板：复选面板调整激活工具集，选择持久化、分支恢复 |
| `confirm_destructive` | 会话切换 / 分叉前的确认门，防止误丢当前会话 |

### 工具（`backend/tools/`，2 个）

| 工具 | 说明 |
|------|------|
| `question` | 交互式询问用户（1~4 问，选项 + 自由输入，TUI 弹对话框） |
| `todo` | 任务清单管理（全量替换语义，分支安全） |

### UI 原语（`backend/nova_base/ui_primitives.py`）

UI 标准原语词汇的官方定义点：`select` / `select_items` / `confirm` / `input` / `form` / `notify_message` / `set_status` 糖库。harness 的 `UIContext` 是零词汇泛型 transport，交互词汇的定义权归包——本模块即基线词汇的落点，其他包（如 nova-coding-agent 的扩展）经 `nova_base.ui_primitives` 复用。

### 前端（`frontend/`）

TUI 呈现半区：6 件 slash 命令 UI（tree / todos / model / scoped-models / resume / fork 选择器）、2 件对话框（question 单框 / tools 复选面板）、2 件工具渲染器（question / todo）。

## 安装

经 `nova-pkg` 安装：

```bash
nova-pkg install /path/to/nova_base
```

安装到用户级（`~/.nova/agent/packages/`，`--local` 装到项目级 `<cwd>/.nova`）。会话启动时自动加载本包的扩展与工具。

## 目录结构

```
nova_base/
├── pyproject.toml           # Python 身份 + [tool.nova] 资源清单
├── backend/
│   ├── nova_base/           # 可导入 Python 包（ui_primitives.py 原语糖库）
│   ├── tools/               # question / todo（单文件形态）
│   ├── extensions/          # 3 个扩展（单文件形态）
│   └── tests/               # pytest
└── frontend/                # 前端半区（Node 宿主加载，自含 TS 子包）
    ├── tui/
    │   ├── index.ts         # 扩展入口（6 个 slash 命令 UI + 2 个对话框注册）
    │   ├── extensions/session_commands/slash/   # 选择器组件
    │   ├── dialogs/         # question / tools 对话框
    │   └── tools/           # question / todo 渲染器
    └── tests/               # TS 测试（镜像 tui/）
```

## License

MIT
