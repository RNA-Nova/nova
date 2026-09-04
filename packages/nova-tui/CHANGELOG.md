# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/lang/zh-CN/).

## [0.2.0] - 2026-09-04

> 首个封版。`nova` 命令（TUI 宿主）与前端运行时同包发布；仓库级变更明细见根 `CHANGELOG.md`。

### Added

- **TUI 宿主（`nova` 命令）**：消息转录（Markdown 渲染、链接可点）、工具调用卡片流式渲染（状态色 header + `ctrl+o` 全局展开/折叠）、启动区与已加载资源区、三行 footer（cwd/git 分支/会话名、token 与上下文用量统计、扩展状态行）。
- **会话能力**：会话树导航（`/tree`）、消息分叉（`/fork`）、历史恢复（`/resume`/`--continue`/`--session`）、克隆（`/clone`）、上下文压缩（`/compact` + 自动压缩）、JSONL 导入导出。
- **消息队列**：steering 插话（Enter）与 follow-up 排队（Alt+Enter），Esc 中断自动还原队列；注入策略可配。
- **模型交互**：`/login`/`/logout` 鉴权管理（OAuth 授权等待框）、`/model` 选择器（`ctrl+l`）、scoped 模型池循环（`ctrl+p`/`shift+ctrl+p`）、思考级别循环（`shift+tab`）与 thinking 块折叠（`ctrl+t`）。
- **编辑器**：`/` 命令补全（三源合并目录）、`@` 文件模糊补全、会话 bash（`!`/`!!`）、剪贴板图片粘贴（`ctrl+v`）、外部编辑器（`ctrl+g`）、挂起后台（`ctrl+z`）。
- **设置面板**：`/settings` 两级选择器可视化编辑 18 项设置（后端 settings、会话态、前端 settings.json 三域分储）。
- **主题系统**：内建 dark/light + `automatic` 跟随终端亮暗；用户目录与包两源自定义 JSON 主题；`/theme` 选择器移动即预览；主题文件 watcher 热更新。
- **键位系统**：user/project 两级 `keybindings.json` 按动作整体重映射；`/hotkeys` 查看生效表；扩展快捷键最优先匹配、保留动作禁覆盖。
- **会话导出与分享**：`/export` 自包含 HTML 导出（主题注入、内联渲染引擎与语法高亮）、`/share` GitHub secret gist 分享（可 Esc 取消）。
- **终端集成**：OSC 0 窗口标题、OSC 9;4 任务栏进度、回复完成桌面通知（OSC 9/777/99）、tmux 键位检测。
- **扩展 UI**：`ExtensionUIAPI` 注册面（工具渲染器/自定义条目/块类型/对话框/命令/快捷键/补全源/区域部件/浮层/编辑器替换）；声明式块词汇（diff/markdown/code/json/table + 开放集）；散养资产双根扫描（user 恒可信、project 过 Project Trust 门）；反向原语五件对话框（select/confirm/input/notify/form）+ `set_status` 状态行 + `dialog:*` 自定义对话框能力宣告。
- **内建命令**：`/help`（三源合并目录查看器）、`/theme`、`/settings`、`/copy`、`/hotkeys`、`/debug`、`/share`、`/changelog`、`/quit`、`/packages`（包管理面板）。
- **前端域目录**：`~/.nova/agent/frontend/tui/` 与 `<cwd>/.nova/frontend/tui/` 两级半区（settings/state/keybindings/themes/debug + 散养资产）；旧路径（ui-settings.json/ui-state/keybindings/themes）启动自动迁移。
