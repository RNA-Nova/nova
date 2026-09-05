# 配置参考

## 目录布局

```
~/.nova/agent/                  # 全局根（NOVA_AGENT_DIR 可覆盖）
├── settings.json               # 全局设置（写门：SettingsManager）
├── auth.json                   # provider 鉴权（OAuth token / API key）
├── models.json                 # 自定义 provider/模型
├── trust.json                  # 项目信任记录
├── sessions/--<cwd>--/         # 会话 JSONL（按工作目录分）
├── packages/                   # 已装包（path/git/npm 三族目录 + .site/ 依赖落点）
├── builtin/nova_base/          # 二进制形态内建包的首启落地处
├── install/                    # install.sh 装的程序本体（releases/ + current 链接）
├── backend/                    # 散养后端资源：extensions/ skills/ prompts/ personas/
├── agents/                     # 散养 agent 组合声明 *.yaml
├── frontend/tui/               # 前端域：settings.json / state/ / keybindings.json / themes/ / debug/
│   ├── tools/  dialogs/  index.ts   # 散养前端资产（渲染器/对话框/扩展入口）
├── logs/                       # 遥测与日志
└── bin/                        # 托管二进制（fd 等）

<cwd>/.nova/                    # 项目级（同名结构，settings.json 不动旧位）
└── backend/  agents/  frontend/tui/
```

## settings.json 键表（常用）

| 键 | 类型 | 说明 |
|----|------|------|
| `default_provider` / `default_model` | string | 默认模型（解析链见[模型与鉴权](models.md)） |
| `default_thinking_level` | string | off/minimal/low/medium/high（按模型能力） |
| `thinking_budgets` | object | 各级别 token 预算（minimal/low/medium/high） |
| `steering_mode` / `follow_up_mode` | `all` / `one-at-a-time` | 运行中插话/跟进的投递策略 |
| `compaction` | object | `reserve_tokens`（触发水位余量）等压缩参数 |
| `branch_summary` | object | 分支摘要：开关/超时/重试 |
| `retry` | object | `max_retries` / `base_delay_ms` / `provider` 细分 |
| `hide_thinking_block` | bool | 转录里折叠 thinking 块 |
| `show_cache_miss_notices` | bool | 缓存未命中提示 |
| `shell_path` / `shell_command_prefix` | string | bash 工具使用的 shell 与命令前缀 |
| `quiet_startup` | bool | 安静启动（少横幅） |
| `collapse_changelog` | bool | 折叠启动时的 changelog |
| `default_project_trust` | `always` / `never` / `ask` | 无 UI 场景的默认信任策略（缺省 ask） |
| `packages` | array | 包清单（字符串源或 `{source, editable, filters…}`） |
| `extensions` / `skills` / `prompts` / `agents` / `tools` / `user_tools` | array | 资源**排除/路径**名单（`!pattern` 排除、路径启用） |

设置面板（`/settings`）覆盖常用项，改动即持久化；面板管不到的键可手改 JSON（改后 `/reload`）。

## 前端设置（`frontend/tui/settings.json`）

UI 域设置与后端分离：主题、编辑器偏好、键位自定义（`keybindings.json`）等——由 TUI 宿主自持，不进后端 settings。

## 环境变量

全表见[环境变量参考](../reference/env-vars.md)。最常用的：

- `NOVA_AGENT_DIR`——后端状态根整体搬迁；
- `NOVA_PYTHON`——pip/开发渠道指定后端解释器；
- `NOVA_OFFLINE=1`——离线模式（禁一切网络动作）；
- `NOVA_BACKEND`——TUI 显式指定后端二进制路径。

## 多机/团队共享

- settings 的 `packages` 里 path 源按**相对路径**持久化——settings.json 可提交进团队仓库共享；
- 安装产物（`packages/` 目录）带 `.gitignore`，不进版本控制；
- 项目级 `.nova/settings.json` 同理可共享（信任门控保护：新机器首次打开项目会问）。
