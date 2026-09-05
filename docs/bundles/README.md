# Bundle 开发：概览与生命周期

Nova 的一切能力——工具、扩展、agent 组合、人格、模板、用户工具、前端渲染器——都经 **bundle（包）** 分发。框架零内置：官方 `nova-base` / `nova-coding-agent` 与第三方包走**完全同一套机制**（官方包只是内建于发布形态）。

## 两种包形态

### A 型：Python 复合包（`pyproject.toml` 为身份证）

```
my-bundle/
├── pyproject.toml          # [tool.nova] 段声明资源（见 manifest.md）
├── agents/                 # agent 组合声明 *.yaml（可选）
├── backend/                # Python 半区
│   ├── tools/              #   LLM 工具（单文件 <name>.py 或目录 <name>/executor.py）
│   ├── extensions/         #   扩展（同形态纪律）
│   ├── user_tools/         #   用户工具（`!` 直执等人类触发工具）
│   ├── personas/           #   人格文本 .md（目录递归收，命名=相对路径去后缀）
│   ├── prompts/            #   用户模板（/命令宏，$@ 占位）
│   ├── skills/             #   技能（SKILL.md 目录）
│   └── my_bundle/          #   包自身 Python 模块（供工具/扩展 import 共享代码）
└── frontend/               # TS 半区（可选；TUI 渲染资产）
    ├── package.json        #   有它才触发 npm 依赖装配
    └── tui/
        ├── tools/          #   工具渲染器 <tool>.ts（文件名即工具名）
        ├── dialogs/        #   自定义对话框 <name>.ts（注册 dialog:<name> slot）
        ├── index.ts        #   前端扩展入口（ExtensionUIAPI 工厂）
        └── lib/            #   辅助模块（不进发现面）
```

### B 型：纯 TS 包（`package.json` 为身份证）

无 Python 半区——只有渲染器/对话框/前端扩展。包根即前端半区（渲染器归 `tui/tools/`）。前后端作者解耦发布的形态。

## 生命周期全链路

```
安装（nova-pkg install）
  ├─ 来源解析：path/git/npm 三源 → 本地目录
  ├─ 校验：manifest 合法性 + requires 门（被依赖包不在即拒）
  ├─ 依赖装配：pip 依赖（二进制形态 → .site/）+ npm 依赖（frontend/package.json）
  │            + 二进制依赖（PyPI wheel / 框架托管注册表）
  ├─ 落位：<agent_dir>/packages/<族>/<名>/ + <名>.dist-info/（安装事实快照）
  └─ 登记：settings.json 的 packages 清单（唯一选择层）

加载（会话装配 / /reload）
  ├─ 冻结形态先挂 sys.path（.site/ + 各包 backend/）
  ├─ PackageResolver 读 settings 清单 → 解析各包资源路径（三来源合并去重，
  │   project > user，autoload:false 的 project delta 翻转）
  ├─ 各 loader 按类目加载：tools/extensions/user_tools/personas/prompts/skills/agents
  ├─ 前端（TUI 独立进程）：发现 frontend/tui/ 渲染器与入口 → jiti 加载
  │   （缺 npm 依赖 → 后台自愈补装 → 上线）
  └─ 激活过滤：denylist → settings allowlist → agent yaml 白名单（三态）

运行
  ├─ 工具：模型 tool_call → 框架校验参数（jsonschema）→ Tool.execute()
  ├─ 扩展：事件总线驱动（session_start/tool_call/…）+ ctx 动作面
  └─ 前端：工具结果 → 渲染器（ToolCallItem → 组件）→ 转录区卡片

更新 / 卸载
  ├─ 更新按来源族回各自源头（git fetch+reset / npm registry；path 不更新）
  └─ 卸载：requires 守护 + 基础包守护 → 删副本 + dist-info + 摘 settings 条目
```

## 发现机制要点（写包前必须知道）

- **只来自已安装包**：没有"散养 tools 自动发现"（散养只有 extensions/skills/prompts/personas 与前端资产）——tools/user_tools 必须经包分发；
- **位置即语义**：`tools/bash.py` 的文件名就是工具名；`frontend/tui/tools/bash.ts` 的文件名就是它渲染的工具；
- **单文件优先、目录按需**：`tools/x.py` 不够用（需要同目录资产/子模块）时才用 `tools/x/executor.py`；
- **元数据即代码**：工具的 `name`/`description`/`parameters` 是 `Tool` 类属性，import 即可读——没有单独的元数据文件。

## 官方包作为活例

读文档时对照看：

- `bundles/nova_base/`——A 型最小完整样例（2 工具 + 3 扩展 + 前端入口）；
- `bundles/nova_coding_agent/`——A 型全形态（8 工具 + 4 扩展 + 5 agent 声明 + personas/prompts + 用户工具 + 子代理引擎）。

下一页：[包清单 `[tool.nova]` 全字段](manifest.md)。
