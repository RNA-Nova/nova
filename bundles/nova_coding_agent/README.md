# nova-coding-agent

Nova 官方编程 Agent bundle：把 `nova_harness` 装配成开箱即用的编程助手。本包提供组合声明（agents）、10 个本地工具、bash 用户工具、7 个会话扩展、persona 人格文本与 prompt 模板，经 `nova-pkg` 安装后，会话启动时自动加载——`nova` 终端界面与 `nova-harness run` 即刻获得完整的编程 Agent 能力。

本包同时是一个可 import 的 Python 包（import 名 `nova_coding_agent`），承载工具链共享的执行引擎与基础设施。

## 内容

### Agents（`agents/`）

纯选配的组合声明（yaml），选人格、选能力名单，不附着内容：

- `coding_agent` —— 主 Agent：完整工具链 + 全部扩展，默认角色。
- `scout` / `planner` / `reviewer` / `worker` —— 子代理四件套，供 `subagent` 工具按名委派：侦察（只读探查代码库）、规划（只读规划）、评审（代码评审）、执行（全能力执行，显式不含 `subagent` 防递归）。

### 工具（`backend/tools/`，10 个）

| 工具 | 说明 |
|------|------|
| `bash` | 在当前工作目录执行 bash 命令，输出截断保护，全量输出落临时文件可续读 |
| `read` | 读取本地文件（文本分页 / 图片），自动判断文件类型 |
| `write` | 写入本地文件，自动创建缺失的父目录 |
| `edit` | 精确文本替换编辑，逐处唯一匹配，生成 diff |
| `grep` | 文件内容搜索（尊重 .gitignore，`rg` 加速、纯 Python 兜底） |
| `find` | 递归查找文件或目录（`fd` 加速、pathlib 兜底） |
| `ls` | 目录条目列表（字母序、含 dotfiles） |
| `question` | 交互式询问用户（1~4 问，选项 + 自由输入，TUI 弹对话框） |
| `todo` | 任务清单管理（全量替换语义，分支安全） |
| `subagent` | 子代理委派：single / parallel / chain 三模式，消费会话 agents 注册表 |

### 用户工具（`backend/user_tools/`）

- `bash` —— 用户在会话内以 `!` 前缀直接执行 bash，与 LLM 的 bash 工具共享同一执行引擎。

### 扩展（`backend/extensions/`，7 个）

| 扩展 | 说明 |
|------|------|
| `session_commands` | 21 个会话 slash 命令：/help、/model、/login、/logout、/compact、/tree、/fork、/resume、/agent、/persona、/trust 等 |
| `permission_gate` | 工具调用拦截：bash 危险命令执行前询问，写保护路径拦截 |
| `plan_mode` | 只读规划模式：/plan 切换，写工具禁用、bash 限只读白名单，编号计划提取与进度跟踪 |
| `tools_panel` | /tools 工具开关面板：复选面板调整激活工具集，选择持久化、分支恢复 |
| `interactive_shell` | 交互式命令终端让位：vim / htop / ssh 等命令挂起 TUI 直接执行 |
| `confirm_destructive` | 会话切换 / 分叉前的确认门，防止误丢当前会话 |
| `subagent_gate` | 子代理委派自治权检查点：逐名裁决允许一次 / 本会话始终允许 / 取消 |

### Personas（`backend/personas/`）

人格文本资源：`coding/core.md` 主人格 + `subagents/`（scout / planner / reviewer / worker）四件子代理人格。

### Prompt 模板（`backend/prompts/`）

用户模板：`debug` / `refactor` 两件通用模板 + subagent 工作流三件套（`implement`＝scout→planner→worker、`scout-and-plan`、`implement-and-review`）。

### 前端（`frontend/`）

自含 TS 子包，供 Node 宿主（TUI）加载：10 件工具渲染器（`tui/tools/`，文件名即工具名）、3 件包侧对话框（`tui/dialogs/`：question / tools / interactive-shell）、6 件 slash 命令 UI（`tui/extensions/session_commands/slash/`：tree / todos / model / scoped-models / resume / fork）。

## 安装

经 `nova-pkg` 安装（`nova_harness` 自带的包管理器，支持 path: / git: / npm: 三种来源，裸路径按 path: 处理）：

```bash
nova-pkg install /path/to/nova_coding_agent
```

安装到用户级（`~/.nova/agent/packages/`，`--local` 装到项目级 `<cwd>/.nova`），并记录到 settings 的包来源清单。会话启动时，`nova_harness` 从已安装包注册表发现并加载本包的 agents / tools / extensions / personas / prompts / user_tools；`agents/coding_agent.yaml` 成为默认角色（也可用 `nova --agent <name>` 或会话内 `/agent` 显式切换）。

安装时会自动执行 `pip install -e .` 把 bundle 的 Python 半区装入环境（tools 借此 `import nova_coding_agent.tools_common` 等共享模块），并按 `binary_dependencies` 声明安装 `rg` 的 PyPI wheel、按 `binary_managed_dependencies` 声明下载 `fd` 到 Nova 托管 bin 目录（见下文「可选依赖」）。前端半区的 npm 依赖在安装的 npm 阶段自动装好。

## 目录结构

```
nova_coding_agent/
├── pyproject.toml           # Python 身份 + [tool.nova] 资源清单
│
├── agents/                  # 组合层（纯选配 yaml，一文件一 agent）
│   ├── coding_agent.yaml    # 主 agent（人格 + 能力名单 + 人格默认模型）
│   └── scout.yaml / planner.yaml / reviewer.yaml / worker.yaml
│                            # 子代理四件套（worker 显式不含 subagent 防递归）
│
├── backend/                 # 后端半区（Python 宿主加载）
│   ├── nova_coding_agent/   # 可导入 Python 包（tools_common/ 工具基建、bash/ 引擎、
│   │                        #   subagent/ 执行引擎、ui_primitives.py 原语糖库）
│   ├── tools/               # 10 个 LLM 工具（单文件形态）
│   ├── user_tools/          # bash 用户工具
│   ├── extensions/          # 7 个扩展（单文件形态）
│   ├── personas/            # 人格文本（coding/core.md + subagents/ 四件套）
│   ├── prompts/             # prompt 模板（debug/refactor + 工作流三件套）
│   └── tests/               # pytest（镜像 backend/ 目录）
│
└── frontend/                # 前端半区（Node 宿主加载，自含 TS 子包）
    ├── package.json         # npm 清单（nova-pkg 安装 npm 阶段的触发点）
    ├── tui/                 # TUI 宿主段（镜像后端资源类型目录）
    │   ├── index.ts         # 扩展入口（6 个 slash 命令 UI + 3 个对话框注册）
    │   ├── tools/           # 工具渲染器（10 件，文件名即工具名）
    │   ├── dialogs/         # 包侧自定义对话框（question / tools / interactive-shell）
    │   ├── extensions/session_commands/slash/   # 命令 UI（镜像后端扩展归属）
    │   └── lib/             # 跨模块共享件
    └── tests/               # TS 测试（镜像 tui/ 目录）
```

## 可选依赖

`grep` 与 `find` 工具优先使用外部二进制加速（`rg` / `fd`），未命中时自动回退到纯 Python 实现，不影响可用性。安装本包时会按 `pyproject.toml` 的声明自动备好（`rg` 走 PyPI wheel，`fd` 走 Nova 托管注册表）；也可手动安装：

```bash
# macOS
brew install ripgrep fd

# Debian/Ubuntu
sudo apt-get install ripgrep fd-find
```

## 开发自己的工具与扩展

工具、用户工具与扩展的作者契约（`Tool` / `UserTool` 类形态、`[tool.nova]` 资源类目、发现与加载纪律）见 [packages/nova_harness/README.md](../../packages/nova_harness/README.md#包与扩展开发) 的「包与扩展开发」一节——本包的 `backend/` 目录即是官方参考实现。

## 开发

```bash
# Python 侧（monorepo 根目录）
pixi run -e dev test-coding          # 或：cd backend && pytest tests

# TS 侧
cd frontend && npm test && npm run typecheck
```

## License

MIT
