# Slash 命令

命令有三个来源：**bundle 扩展注册**（`nova-base` 的会话命令、`nova-coding-agent` 的 `/tools`、`/plan`）、**TUI 宿主内建**（界面域）、**第三方包**。输入 `/` 时编辑器自动补全；`/help` 在会话内列出当前可用的实时清单（含第三方包注册的命令）。

## 会话（nova-base）

| 命令 | 说明 |
|------|------|
| `/new` | 创建新会话 |
| `/session` | 显示当前会话信息（ID、条目数、模型、token 用量） |
| `/name [名称]` | 设置/查看会话显示名 |
| `/clone` | 克隆当前会话 |
| `/resume` | 浏览并恢复已有会话（选择器） |
| `/fork [entry_id] [at\|before\|after]` | 从某条消息分叉会话（无参数弹选择器） |
| `/tree [target_id]` | 导航会话树（无参数弹树选择器） |
| `/export [路径]` | 导出会话（TUI 下无参数或 `.html` 走 HTML 导出；`.jsonl` 导出原始条目） |
| `/import <路径>` | 从 JSONL 导入会话 |
| `/compact` | 手动压缩上下文（生成摘要替换历史） |

## 模型与鉴权

| 命令 | 说明 |
|------|------|
| `/model [provider/id]` | 切换/查看当前模型（无参数弹选择器） |
| `/scoped-models` | scoped 模型池面板（`ctrl+p` 循环启用集与顺序） |
| `/login [provider]` | 配置 provider 认证（OAuth 或 API key） |
| `/logout [provider]` | 移除 provider 认证 |

## 角色与人格

| 命令 | 说明 |
|------|------|
| `/agent [name]` | 切换角色（弹选择器；列表含来源标签） |
| `/agent save` / `/agent save-as <name>` | 把当前生效状态（人格/工具/模型偏好）物化回组合声明 yaml |
| `/persona [name\|default]` | 切换会话人格（内存态覆盖，分支持久化） |

## 工具与计划（nova-coding-agent）

| 命令 | 说明 |
|------|------|
| `/tools` | 工具开关面板（复选框；选择随分支持久化） |
| `/plan` | 切换只读规划模式（写工具禁用、bash 限只读白名单） |
| `/todos` | 查看当前分支的 todo 清单（TUI 弹模态查看器；运行中也可用）——nova-base |

## 资源与其他（nova-base）

| 命令 | 说明 |
|------|------|
| `/reload` | 重新加载资源与扩展（装了新包/改了扩展后） |
| `/help` | 列出全部可用命令 |

## 信任（nova-base）

| 命令 | 说明 |
|------|------|
| `/trust` | 信任当前项目（加载 `<cwd>/.nova` 下的资源） |
| `/untrust` | 取消信任 |

## TUI 内建（宿主本地命令，不经过后端扩展）

| 命令 | 说明 |
|------|------|
| `/theme` | 主题选择与预览（移动即预览，Enter 持久化） |
| `/settings` | 可视化设置面板 |
| `/packages` | 包面板（详情/更新/卸载——`nova-base` 不提供卸载动作） |
| `/changelog` | 查看更新日志（What's New） |
| `/copy` | 复制最后一条回复到剪贴板 |
| `/hotkeys` | 查看全部键位（含自定义重绑定） |
| `/debug` | 镜像状态 dump 到 `frontend/tui/debug/debug-*.log` |
| `/share` | 分享会话为 secret gist（需 gh CLI） |
| `/quit` | 退出 |

## bash 直执（不是命令，是前缀）

- `!命令`——在当前 shell 执行，输出**不进入**模型上下文；
- `!!命令`——执行并把输出**送入**上下文（模型可见）。

交互式程序（vim/htop/ssh 等）用 `i <命令>` 前缀或直接被识别——TUI 让位给原生终端，退出后恢复（`nova-coding-agent` 的 interactive-shell 能力）。
