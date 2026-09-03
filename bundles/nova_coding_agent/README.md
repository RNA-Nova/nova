# nova-coding-agent

Nova 官方编程 Agent bundle，提供本地文件系统操作与命令执行能力。

## 安装

这是一个 Nova bundle，同时也是一个 Python 包，通过 `nova-pkg` 安装：

```bash
nova-pkg install /path/to/nova_coding_agent
```

安装时会自动读取 `pyproject.toml` 并安装本地 path 依赖（`nova-ai`、`nova-agent`、`nova-harness`），同时把 bundle 自身作为 Python 包装入环境（`--no-deps` 自安装），使 tools 可以 `import nova_coding_agent.tools_common`。

## 目录结构（三段式：backend/ + frontend/ + agents/ 组合层）

```
nova_coding_agent/
├── pyproject.toml           # Python 身份 + [tool.nova] 资源清单（路径指向 backend/ 与 agents/）
│
├── agents/                  # 组合层（纯选配 yaml，一文件一 agent）
│   ├── coding_agent.yaml    # 主 agent（人格 + 能力名单 + 人格默认模型）
│   └── scout.yaml / planner.yaml / reviewer.yaml / worker.yaml
│                            # subagent 四件套（供 subagent 工具按名调用；
│                            #   worker 显式不含 subagent 防递归）
│
├── backend/                 # 后端半区（harness/Python 宿主加载）
│   ├── nova_coding_agent/   # 可导入 Python 包（tools_common/ 工具基建、bash/ 引擎、
│   │                        #   subagent/ 执行引擎、ui_primitives.py 原语糖库）
│   ├── tools/               # 10 个 LLM 工具（单文件形态：
│   │                        #   bash/edit/find/grep/ls/question/read/subagent/todo/write）
│   ├── user_tools/          # bash 用户工具
│   ├── extensions/          # 6 个扩展（单文件形态）：
│   │                        #   session_commands（19 个 slash 命令）/ permission_gate（危险拦截）
│   │                        #   plan_mode（只读规划模式）/ tools_panel（/tools 开关面板）
│   │                        #   interactive_shell（交互命令终端让位）/ confirm_destructive（切换确认）
│   ├── personas/            # 人格文本素材（coding/core.md + subagents/ 四件套）
│   ├── prompts/             # prompt 模板（debug/refactor + implement 等工作流三件套）
│   └── tests/               # pytest（镜像 backend/ 目录）
│
└── frontend/                # 前端半区（Node 宿主加载，自含 TS 子包）
    ├── package.json         # npm 清单（nova-pkg 安装第 4 阶段触发点）
    ├── tsconfig.json
    ├── tui/                 # TUI 宿主段（镜像后端资源类型目录）
    │   ├── index.ts         # 扩展入口（ExtensionUIAPI 工厂：6 个 slash 命令 UI + 3 个 dialog 注册）
    │   ├── tools/           # 工具渲染器（10 件——返回活 pi-tui 组件，文件名即工具名）
    │   ├── dialogs/         # 包侧自定义对话框（dialog:* slot：question / tools / interactive-shell）
    │   ├── extensions/session_commands/slash/{tree,todos,model,scoped-models,resume,fork}/
    │   │                    # 命令 UI（镜像后端扩展归属）
    │   └── lib/             # 跨模块共享件（edit-preview 匹配引擎）
    └── tests/               # TS 测试（tsx --test，镜像 tui/ 目录）
```

## 可选依赖

`grep` 与 `find` 工具优先使用外部二进制以获得更好性能（如 `rg`、`fd`）。未安装时会自动回退到纯 Python 实现。可手动安装：

```bash
# macOS
brew install ripgrep fd

# Debian/Ubuntu
sudo apt-get install ripgrep fd-find
```

## 开发

```bash
cd /path/to/nova_coding_agent
# Python 侧
pixi run -e dev test-coding          # 或：cd backend && pytest tests
# TS 侧
cd frontend && npm test && npm run typecheck
```
