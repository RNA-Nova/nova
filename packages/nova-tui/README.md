# nova-tui

Nova 的终端用户界面（TUI）。一个 TypeScript 厚应用层：以 JSON-RPC over stdio 连接 Python 后端 `nova_harness`（会话、模型、工具、扩展的全部事实源在后端），本地承担全部呈现与交互——消息转录、工具卡片、对话框、主题、键位、会话导出。安装后提供 `nova` 命令。

## 特性

- **会话树导航**：`/tree` 跳转任意分支、`/fork` 从任意用户消息分叉、`/resume` 恢复历史会话、`/clone` 克隆当前会话——会话以 JSONL 分支树持久化，断电可续。
- **工具卡片流式渲染**：工具调用实时成卡（状态色 header + 参数摘要 + 输出），`ctrl+o` 全局展开/折叠；官方 bundle 为 bash/edit/read/write 等 10 个工具与子代理委派提供专用渲染器。
- **消息队列**：agent 工作中可继续输入——Enter 插话（steering）、Alt+Enter 排队（follow-up），Esc 中断时排队内容自动还原进编辑器。
- **模型与思考级别**：`/model` 选择器、scoped 模型池 `ctrl+p` 循环、思考级别 `shift+tab` 循环、`ctrl+t` 折叠 thinking 块。
- **主题系统**：内建 dark/light + `automatic` 跟随终端亮暗；自定义 JSON 主题（用户目录与包两个来源），选择器移动即预览，文件热更新。
- **键位自定义**：user/project 两级 `keybindings.json`，按动作整体重映射，`/hotkeys` 查看当前生效值。
- **会话导出与分享**：`/export` 导出自包含 HTML（或 JSONL），`/share` 一键发 GitHub secret gist。
- **终端集成**：OSC 0 窗口标题、OSC 9;4 任务栏进度、回复完成桌面通知、剪贴板图片粘贴、外部编辑器、挂起后台（ctrl+z）。
- **可视化设置面板**：`/settings` 两级选择器编辑 18 项设置（主题/双 Esc/消息注入策略/终端集成等），选中即生效并持久化。
- **扩展 UI**：包与散养目录可贡献工具渲染器、自定义对话框、命令、快捷键、区域部件与主题；项目级资产经 Project Trust 门控。

## 目录

- [安装](#安装)
- [快速上手](#快速上手)
- [CLI 参考](#cli-参考)
- [界面](#界面)
- [编辑器](#编辑器)
- [消息队列与中断](#消息队列与中断)
- [会话管理](#会话管理)
- [模型与鉴权](#模型与鉴权)
- [主题](#主题)
- [命令参考](#命令参考)
- [键位参考](#键位参考)
- [设置](#设置)
- [配置文件](#配置文件)
- [终端集成](#终端集成)
- [会话导出与分享](#会话导出与分享)
- [扩展 UI（给扩展作者）](#扩展-ui给扩展作者)
- [开发](#开发)
- [License](#license)

## 安装

```bash
npm install -g nova-tui
```

前置条件：

- **Node.js `>=22.19.0`**；
- **Python `>=3.12` 环境可 import `nova_harness`**（`pip install nova-harness`，连带 `nova-ai`、`nova-agent`）——TUI 启动时以子进程方式 spawn `<python> -m nova_harness.modes.rpc.cli` 作为后端。默认解释器为 `python3`，可用环境变量 `NOVA_PYTHON` 指定其他解释器路径（如虚拟环境或 pixi 环境的 python）。

后端启动命令解析链（`wire/backend-command.ts`，从高到低）：`NOVA_BACKEND`（显式指定后端二进制路径）→ 同目录 `runtime/nova-server`（打包形态：`nova` 二进制旁的随行后端，此时无需任何 Python 环境）→ `NOVA_PYTHON` → `python3` 模块调用。

Agent 能力（工具、slash 命令、子代理等）由已安装的 Nova 包提供——官方双 bundle `nova-base`（会话基础设施）+ `nova-coding-agent`（编程执行）经 `nova-pkg install` 安装后即可在 TUI 中使用；打包形态下 nova-base 内建随行（首启即用），nova-coding-agent 按需 `nova-server pkg install` 安装。

## 快速上手

```bash
nova                          # 在当前目录启动
nova --continue               # 继续当前目录最近一次会话
nova 帮我审查 src 目录         # 带首条消息启动（@file 展开为文件内容）
```

首次启动且无任何可用模型时，会弹出引导（登录 provider / 选择默认模型）。典型流程：

1. **`/login`** —— 配置 provider 鉴权（OAuth 授权或 API key；也可直接设环境变量，如 `VOLCENGINE_API_KEY`）；
2. **`/model`** —— 选择模型（选择器或直接 `/model provider/id`）；
3. 直接输入消息与 agent 对话；
4. **`!命令`** —— 会话 bash（输出进入上下文）；`!!命令` 执行但不进上下文；
5. **`/`** —— 命令入口，输入即出补全；`/help` 查看全部可用命令。

退出：`ctrl+c` 双击、`ctrl+d`（空编辑器）或 `/quit`。退出后按提示 `nova --continue` 恢复会话。

## CLI 参考

```
nova [选项] [message...]
```

`[message...]` 为启动后立即发送的首条消息；其中以 `@` 前缀的词元按文件参数展开——文本文件内联进消息，图片文件（png/jpeg/gif/webp，魔数嗅探）作为附件发送。

| 选项 | 说明 |
|------|------|
| `-c, --cwd <dir>` | 工作目录（默认当前目录） |
| `-m, --model <ref>` | 指定模型（`provider/id`） |
| `-a, --agent <name>` | 指定 Agent 名称 |
| `--continue` | 继续当前目录最近一次会话 |
| `-r, --resume` | 启动后打开会话选择器 |
| `--session <file\|id>` | 恢复指定会话（文件路径或会话 id） |
| `-n, --name <name>` | 设置会话名 |
| `--thinking <level>` | 思考级别（off/minimal/low/medium/high/xhigh/max） |
| `--no-session` | 不持久化会话（内存态，不落盘不进会话列表） |

`--session`、`--continue`、`--resume` 三者互斥。

## 界面

自上而下五个区域：

- **启动区**：logo、版本、当前模型与工作目录、键位提示（随 `ctrl+o` 展开态切换详略）；其下是已加载资源区（skills/prompts/commands/packages 计数，展开态逐行列出）。`quietStartup` 设置可关闭整个启动区。
- **消息区（transcript）**：用户消息、助手回复（Markdown 渲染，链接可点）、工具调用卡片、通知与错误。工具结果中的图片在支持图片协议的终端内联渲染。
- **状态区**：工作中的 loader 与耗时、重试倒计时、压缩指示。
- **编辑器**：输入区。边框颜色即状态——绿色为 bash 模式（`!` 开头），其余按当前思考级别着色。
- **footer（三行）**：① 工作目录（git 分支）• 会话名；② token 统计（`↑`输入 `↓`输出 `R`缓存读 `W`缓存写、命中率、成本）• 上下文用量百分比（超 70% 黄、超 90% 红）• 模型 • 思考级别；③ 扩展状态行（后端扩展经 `set_status` 原语写入，如规划模式进度）。

编辑器槽位可被对话框临时替换（`/settings`、选择器、后端弹窗等），应答后恢复。

## 编辑器

| 功能 | 操作 |
|------|------|
| 命令 | 输入 `/` 触发补全（三源合并目录：后端命令 + 本地命令 + 扩展命令；prompt 模板与 skill 分别标注"提示词模板"/"技能"） |
| 文件引用 | 输入 `@` 模糊搜索项目文件；Tab 路径补全 |
| 会话 bash | `!command` 执行并把输出送入上下文；`!!command` 执行但不进上下文 |
| 多行输入 | `shift+enter` 或 `ctrl+j` 换行 |
| 剪贴板 | `ctrl+v` 粘贴；剪贴板中的图片写为临时文件并把路径插入编辑器（模型经 read 工具读图） |
| 外部编辑器 | `ctrl+g` 用外部编辑器编辑草稿（`external_editor` 设置 > `$VISUAL` > `$EDITOR` > `vi`），退出后回写 |
| 历史 | `↑`/`↓` 翻阅提交历史 |

## 消息队列与中断

agent 工作（working）时仍可提交消息：

- **Enter** —— steering 插话：当前回合的工具调用执行完后注入；
- **Alt+Enter** —— follow-up 排队：等 agent 全部工作结束后发送；
- **Esc** —— 中断当前 run，排队消息自动还原进编辑器（不丢）；
- **Alt+↑** —— 手动把队列内容还原进编辑器。

注入策略可在 `/settings` 调整：`steering_mode` 与 `followup_mode` 各支持 `all`（全部注入）/ `one-at-a-time`（逐条）。

Esc 按域路由：对话框开着 → 关闭对话框（不中断 run）；前台任务（gist 分享、分支摘要等）在飞 → 取消任务；否则按会话状态——working 停整个 run、retrying 只停重试、compacting 只停压缩。idle 且编辑器为空时，**双击 Esc** 触发导航（默认打开 `/tree` 会话树，可在 `/settings` 改为 `/fork` 分叉或关闭）。

## 会话管理

- `/new` 新会话；`/resume` 浏览并恢复历史会话；`/session` 查看当前会话信息；`/name` 设置会话名；
- `/tree` 会话树导航（跳到任意节点续聊）；`/fork` 从某条用户消息分叉（`/fork [entry_id] [at|before|after]`）；`/clone` 克隆当前会话；
- `/compact` 手动压缩上下文；上下文接近上限时自动压缩（`auto_compaction` 设置可关）；
- `/export` / `/import` / `/share` 见[会话导出与分享](#会话导出与分享)；
- `/trust` / `/untrust` 项目信任决策——未信任项目的 `.nova` 资源与包不加载（启动横幅提示），决策持久化于 `~/.nova/agent/trust.json`。

会话以 JSONL 分支树存储在 `~/.nova/agent/sessions/--<cwd>--/`，全部导航操作分支安全。

## 模型与鉴权

- `/login [provider]` —— 配置鉴权：OAuth 流程弹授权等待框（自动打开浏览器，Esc 取消），API key 直接输入；凭据存于 `~/.nova/agent/auth.json`；
- `/logout [provider]` —— 移除鉴权；
- `/model [provider/id]` —— 切换模型（无参数弹选择器）；`ctrl+l` 同效；
- `/scoped-models` —— scoped 模型池面板；`ctrl+p` / `shift+ctrl+p` 在池内循环切换（池中已配置凭据的模型不足两个时给出提示）；
- 思考级别：`shift+tab` 循环（按模型支持面），`ctrl+t` 折叠/展开 thinking 块，`--thinking` 启动指定。级别吸附到模型支持集，不支持的模型忽略。

未配置任何可用模型时启动会弹出首次引导；footer 与启动区实时显示当前模型。

## 主题

- `/theme` 打开主题选择器：**移动即预览**（全量重渲），Enter 确认并持久化，Esc 恢复打开前的主题；
- `automatic` 档跟随终端亮暗（初始经 `COLORFGBG` 检测，之后跟随终端配色通知实时切换）；
- 主题三来源（优先级：内建 > 用户目录 > 包）：内建 `dark`/`light`；用户目录 `~/.nova/agent/frontend/tui/themes/*.json`；已安装包的 `frontend/themes/*.json`；
- 主题文件经 watcher 热更新——编辑命中当前主题的 JSON 即重载；
- 主题 JSON 契约见[配置文件](#配置文件)。

## 命令参考

命令来自三个来源，按名去重（覆盖优先级：本地 > 扩展 slot > 后端）；`/help` 分组展示全量。agent 组合声明（yaml）与 settings 可对命令做允许/禁用过滤。

### TUI 本地命令（前端实现，恒可用）

| 命令 | 说明 |
|------|------|
| `/help` | 查看全部可用命令（三源合并，含本地与扩展命令） |
| `/theme` | 切换主题（移动即预览，Enter 持久化） |
| `/settings` | 可视化编辑设置（见[设置](#设置)） |
| `/copy` | 复制最后一条回复到剪贴板（`ctrl+x` 同效） |
| `/hotkeys` | 查看全部键位（含自定义重绑定后的生效值） |
| `/debug` | 镜像状态 dump 到 `frontend/tui/debug/debug-*.log` |
| `/share` | 分享会话为 GitHub secret gist（需 `gh` CLI） |
| `/changelog` | 查看更新日志 |
| `/quit` | 退出 nova |

### 内建扩展命令

| 命令 | 说明 |
|------|------|
| `/packages` | 包管理面板（列表/详情/更新/卸载，user/project 两级切换，可更新包带角标） |

### 官方 bundle 命令（安装 `nova-coding-agent` 后由后端扩展注册）

| 命令 | 说明 |
|------|------|
| `/compact` | 手动压缩会话上下文 |
| `/fork [entry_id] [at\|before\|after]` | 从用户消息分叉会话（无参数弹选择器） |
| `/clone` | 克隆当前会话 |
| `/export <path>` | 导出会话（`.jsonl` 走后端；无参数或 `.html` 走前端 HTML 导出） |
| `/import <path>` | 从 JSONL 导入会话 |
| `/model [provider/id]` | 切换或查看当前模型（无参数弹选择器） |
| `/scoped-models` | 查看 scoped 模型池（弹池面板） |
| `/resume` | 浏览并恢复已有会话（弹选择器） |
| `/login [provider]` / `/logout [provider]` | 配置/移除 provider 鉴权（无参数弹选择器） |
| `/session` | 显示当前会话信息 |
| `/name [display_name]` | 设置或查看会话名称 |
| `/new` | 创建新会话 |
| `/reload` | 重新加载资源与扩展 |
| `/tree [target_id]` | 导航会话树 |
| `/todos` | 查看当前分支的 todo 清单（弹模态查看器） |
| `/persona [name\|default]` | 切换会话人格（无参数弹选择器） |
| `/agent [name\|save\|save-as <name>]` | 切换/保存当前角色（无参数弹选择器） |
| `/trust` / `/untrust` | 信任/取消信任当前项目 |
| `/plan` | 切换规划模式（只读探索，禁用写工具） |
| `/tools` | 工具开关面板（复选提交，绝对集生效） |

其中 `/tree`、`/todos`、`/model`、`/scoped-models`、`/resume`、`/fork` 在 TUI 下由 bundle 前端段提供选择器 UI（后端同名命令保留为无 UI 环境的回退）。

bundle 还提供 prompt 模板（输入后展开发给模型）：`/refactor`、`/implement`、`/implement-and-review`、`/scout-and-plan`、`/debug`（在 TUI 中被同名本地命令遮蔽）。已安装包注册的 prompt 模板与 skill 同样进入命令目录，补全描述分别以"提示词模板"/"技能"标注。

## 键位参考

### 全局键位

| 键位 | 动作 |
|------|------|
| `Esc` | 取消/中断（对话框 > 前台任务 > run/重试/压缩，域级路由） |
| `Esc` 双击（空编辑器） | 导航（默认 `/tree`，可配 `/fork` 或关闭） |
| `ctrl+c` | 清空编辑器；500ms 内双击退出 |
| `ctrl+d` | 编辑器为空时退出 |
| `ctrl+o` | 工具输出展开/折叠（全局，转录/启动区/资源区联动） |
| `ctrl+v` | 粘贴剪贴板（图片 → 临时文件路径） |
| `ctrl+x` | 复制最后一条回复到剪贴板 |
| `ctrl+l` | 打开模型选择器 |
| `ctrl+p` / `shift+ctrl+p` | scoped 模型池循环（前进/后退） |
| `shift+tab` | 循环思考级别 |
| `ctrl+t` | thinking 块显隐切换 |
| `alt+enter` | follow-up 排队（当前工作结束后发送） |
| `alt+↑` | 排队消息还原进编辑器 |
| `ctrl+z` | 挂起到后台（`fg` 恢复；Windows 不支持） |
| `ctrl+g` | 外部编辑器编辑草稿 |

### 编辑器键位（要点）

`enter` 提交；`shift+enter`/`ctrl+j` 换行；`tab` 补全；`ctrl+a`/`ctrl+e` 行首/行尾；`ctrl+b`/`ctrl+f` 左/右移；`alt+b`/`alt+f` 按词移动；`ctrl+w` 删前词；`alt+d` 删后词；`ctrl+u`/`ctrl+k` 删到行首/行尾；`ctrl+y` 召回；`ctrl+-` 撤销。

### 选择器键位

`↑`/`↓` 移动；`enter` 确认；`Esc` 或 `ctrl+c` 取消；可输入字符过滤。

全部键位可按动作重映射（见[配置文件](#配置文件)的 keybindings.json）；`/hotkeys` 查看当前生效表。扩展注册的快捷键最优先匹配，但不得覆盖保留动作（中断/清屏/退出/提交/选择器确认与取消）。

## 设置

`/settings` 打开两级设置面板（第一级选配置项，第二级选值——当前值排第一，选中即生效并持久化，Esc 返回上级）。18 项按存储域分三类：

**后端 settings.json**（`~/.nova/agent/settings.json`，跨前端共享）：

| 配置项 | 说明 | 缺省 |
|--------|------|------|
| `theme` | 主题（`automatic` 跟随终端亮暗） | 按终端亮暗检测（COLORFGBG），兜底 dark |
| `doubleEscapeAction` | 双 Esc 导航（`tree` / `fork` / `none`） | `tree` |
| `quietStartup` | 启动时不显示启动区 | `false` |
| `hideThinkingBlock` | thinking 全文折叠为静态标签（`ctrl+t` 同效） | `false` |
| `showCacheMissNotices` | 显示显著的 prompt 缓存 miss 提醒 | `false` |
| `defaultProjectTrust` | 项目 trust 门默认裁决（`ask` / `always` / `never`） | `ask` |
| `roleBoundary` | 角色边界（`open` 名单只做初始激活集 / `strict` 硬边界） | `open` |

**会话态**（随会话，即时生效）：`steering_mode`、`followup_mode`（消息注入策略，`all` / `one-at-a-time`）、`auto_compaction`（自动压缩开关）、`thinking_level`（思考级别，按模型支持面）。

**前端 settings.json**（`~/.nova/agent/frontend/tui/settings.json`，TUI 私有）：

| 配置项 | 说明 | 缺省 |
|--------|------|------|
| `tree_filter_mode` | `/tree` 选择器初始过滤（default/no-tools/user-only/labeled-only/all） | `default` |
| `branch_summary_skip_prompt` | 分支摘要跳过确认直接执行 | `false` |
| `editor_padding` | 编辑器左右留白列数（0–3） | `1` |
| `autocomplete_max_items` | 补全下拉最大可见行 | `5` |
| `clear_on_shrink` | 内容收缩时清空残余行（慢终端可关） | `true` |
| `terminal_progress` | OSC 9;4 终端进度指示 | `false` |
| `desktop_notify` | 回复完成桌面通知 | `true` |

## 配置文件

### 目录约定

后端状态根为 `~/.nova/agent/`（settings/auth/trust/models/sessions/packages 等）；**前端域**按宿主分级挂在其下的 `frontend/<host>/` 半区，项目级同构：

```
~/.nova/agent/frontend/tui/     # user 级（TUI 宿主）
├── settings.json               # 前端设置（上表第三类）
├── state/                      # 扩展内部 KV（每扩展一个 <命名空间>.json）
├── keybindings.json            # 用户键位表
├── themes/                     # 自定义主题（*.json）
├── debug/                      # /debug 状态 dump
└── tools/  dialogs/  index.ts  # 散养渲染器/对话框/扩展入口（见扩展 UI）

<cwd>/.nova/frontend/tui/       # project 级同构（散养资产过 Project Trust 门）
```

### keybindings.json

```json
{
  "app.tools.expand": "ctrl+o",
  "app.thinking.cycle": ["shift+tab", "f6"],
  "app.suspend": []
}
```

按动作 ID（actionId）**整体替换**默认键（不是追加）；值为键位字符串或字符串数组，空数组表示禁用该动作。两级文件合并：project（`<cwd>/.nova/frontend/tui/keybindings.json`）覆盖 user（`~/.nova/agent/frontend/tui/keybindings.json`）。未知 actionId 与非法值跳过并在启动时给出诊断；项目级键位表是纯声明式映射，不做 trust 门控。动作 ID 与当前绑定见 `/hotkeys`。

### 主题 JSON

```json
{
  "name": "my-theme",
  "vars": { "brand": "#7aa2f7" },
  "colors": {
    "accent": "brand",
    "text": "#c0caf5",
    "selectedBg": 236
  },
  "export": { "pageBg": "#1a1b26", "cardBg": "#24283b", "infoBg": "#1f2335" }
}
```

- `colors` 必须集齐 46 个必需色 token（核心 27 + Markdown 10 + 语法高亮 9），缺失报错并列出清单；多余字段（如 thinking 级别边框色 `thinkingMinimal`…`thinkingMax`）容忍忽略；
- 色值四种形态：`#rrggbb` hex、`""`（终端默认色）、`0`–`255`（256 色索引）、其他字符串 = `vars` 变量引用（环引用报错）；
- `export` 段可选（HTML 导出配色，缺省从 `userMessageBg` 派生）；
- 主题名不得含 `/`；文件置于 `~/.nova/agent/frontend/tui/themes/` 即被 `/theme` 发现（坏文件跳过并诊断）。

### 旧路径迁移

旧版前端状态（`~/.nova/agent/ui-settings.json`、`ui-state/`、`keybindings.json`、`themes/`，项目级 `.nova/keybindings.json`）在 TUI 启动时自动迁入上述 `frontend/tui/` 半区：mv 语义（只搬不删）、幂等、新位已有内容不合并不覆盖（给出提示请人工合并）。

## 终端集成

- **窗口标题（OSC 0）**：`nova - <会话名> - <目录名>`，随会话信息联动；扩展可经 `setTitle` 覆盖；
- **任务栏进度（OSC 9;4）**：working/compacting 时置位（`terminal_progress` 设置，默认关）；
- **桌面通知**：回复完成时发送（OSC 9 / 777 / 99 三序列并发，终端各自识别；`desktop_notify` 设置，默认开）；
- **tmux 兼容**：启动时检测 tmux 键位传递配置，需要时给出提示；
- **挂起**：`ctrl+z` 挂起到后台，`fg` 恢复（Windows 不支持）。

## 会话导出与分享

- `/export [path]` —— 无参数或 `.html` 后缀时导出**自包含 HTML**（单文件，内联渲染引擎与语法高亮，主题配色注入；缺省文件名 `nova-session-<id前8位>.html`）；`.jsonl` 后缀走后端导出原始会话 JSONL；
- `/import <path.jsonl>` —— 导入 JSONL 会话并恢复；
- `/share` —— 导出 HTML 后经 `gh gist create` 发布为 secret gist（需已登录的 GitHub CLI；创建中 Esc 可取消），gist URL 显示在转录中。

## 扩展 UI（给扩展作者）

TUI 的呈现层可扩展，内建能力（官方工具渲染器、块适配器、`/packages` 面板）与第三方扩展走同一套 `ExtensionUIAPI`。

### 资产来源与发现

| 来源 | 位置 | 信任 |
|------|------|------|
| 已安装包 | `<包>/frontend/tui/`（`index.ts`、`tools/<工具名>.ts`、`user_tools/<名称>.ts`）+ `<包>/frontend/themes/*.json` | 随包安装 |
| user 散养 | `~/.nova/agent/frontend/tui/`（`tools/`、`dialogs/`、`index.ts`） | 恒可信 |
| project 散养 | `<cwd>/.nova/frontend/tui/`（同构） | **Project Trust 门控**——未信任不 stat 不 import |

同名覆盖优先级：project > user > package > builtin（碰撞有诊断）。渲染器目录是纯发现域：一文件一工具、默认导出渲染函数（可选 `preview` 命名导出做执行前只读预览）、`*.test.ts` 跳过；辅助模块归 `lib/`。

### 渲染器契约

工具渲染器输入为线上归约后的工具卡片（`input.item`），返回**声明式块数组或活组件**双形态：

- 块词汇内建五种：`diff`（词级高亮）、`markdown`、`code`、`json`、`table`；`registerBlock` 可注册新块类型，未注册的块类型降级为 `json` 展示；
- 组件形态直接返回组件实例（经 `input.env` 取色与主题）。

### 扩展入口（index.ts）

默认导出 `ExtensionUIAPI` 工厂，注册面：

- 内容：`registerRenderer`（工具渲染器）、`registerEntryRenderer`（自定义条目）、`registerBlock`（块类型）、`registerDialog`（自定义对话框）；
- 交互：`registerCommand`（slash 命令，可带参数补全）、`registerShortcut`（快捷键，不得撞保留键位）、`registerAutocompleteProvider`（补全源）；
- 布局：`registerRegion` / `registerRegionComponent`（header/widget/status/widgetBelow 四区域）、`registerOverlay`（浮层）、`registerEditor`（编辑器整件替换）。

扩展上下文（`ctx`）提供宿主原语：对话框（`select`/`confirm`/`input`/`editor`/`custom`）、编辑器读写与粘贴、剪贴板、`setStatus`（footer 状态行）、主题读写、`runInteractive`（终端让位跑交互命令）、`setTitle`、`notifyDesktop`、`setFooter`/`setHeader` 整件替换、loader 三旋钮（`setWorkingMessage`/`setWorkingIndicator`/`setWorkingVisible`）、`events.on` 事件观察口、设置与 KV 存储（`settings`/`state`）；后端能力一律经 `invoke` 调用全量 RPC 方法表。

### 反向原语（后端 → 前端）

后端工具与扩展可主动向用户提问：`select`/`confirm`/`input`/`notify`/`form` 五件基线对话框与 `set_status` 状态行由 TUI 原生实现（词汇定义归包，harness 只做泛型传输）。`registerDialog` 注册 `dialog:<name>` 后自动向前端能力集宣告，后端 `has_capability("dialog:<name>")` 放行——官方 bundle 的 question 工具单框、tools 面板、interactive-shell 终端让位即此形态。

## 开发

```bash
cd packages/nova-tui
npm install
npm run build    # tsc → dist/（含 CHANGELOG 与导出模板资产拷贝）
npm test         # tsx --test "tests/**/*.test.ts"
npm run tui      # tsx 直接运行 TUI（src/modes/tui/main.ts）
npm start        # node 运行编译产物（dist/modes/tui/main.js）
npm link         # 全局注册 nova 命令
```

monorepo 内开发时，用 pixi 环境的 python 作为后端解释器：`NOVA_PYTHON=<repo>/.pixi/envs/dev/bin/python npm run tui`。

## License

MIT
