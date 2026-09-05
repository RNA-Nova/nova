# 快速上手

从安装到第一个能干活的状态，约 5 分钟。

## 1. 安装

```bash
curl -fsSL https://github.com/RNA-Nova/nova/releases/latest/download/install.sh | sh
```

详见[安装与升级](installation.md)（含其他渠道与卸载）。

## 2. 配置模型

启动：

```bash
nova
```

首次启动若无可用鉴权，用 `/login` 配置 provider：

- **OAuth 登录**（推荐，kimi-coding 等）：`/login` → 选 provider → 按引导完成浏览器授权；
- **API key**：`/login` → 选 provider → 粘贴 key（输入以掩码显示，不落明文回显）。

也可直接放环境变量（如 `VOLCENGINE_API_KEY`），或写 `~/.nova/agent/models.json` 自定义 provider/端点（OpenAI 兼容端点均可接入）。

切换模型：`/model`（弹选择器）或 `/model provider/model-id` 直切。

## 3. 第一轮对话

直接输入需求，例如：

```
帮我看看当前目录的结构，说说这个项目是做什么的
```

`nova` 默认进入 `coding_agent` 角色（装了 `nova-coding-agent` bundle 后），模型会调用 bash/read/grep 等工具实际查看你的目录。

几个立刻有用的键位：

- `Esc` 中断当前生成；
- `ctrl+o` 展开/收起工具调用详情；
- `!命令` 直接执行 bash（不进上下文）；`!!命令` 执行并进上下文；
- `/help` 列出全部命令。

## 4. 装编程能力包（按需）

发布形态内建 `nova-base`（slash 命令、todo、question 等会话基础设施）。编程执行能力（bash/edit/grep/subagent 等 8 工具 + 5 个角色）由 `nova-coding-agent` bundle 提供：

```bash
# 列出已装包
nova-pkg list            # pip 渠道；二进制形态用 runtime/nova-server pkg list

# 安装官方编程 bundle（path/git/npm 三种源任选）
runtime/nova-server pkg install path:/path/to/bundles/nova_coding_agent
```

装完重启 `nova`（或 `/reload`），角色选择器（`/agent`）里就有 `coding_agent` 了。

## 5. 接下来读什么

- 日常操作：[TUI 界面](guide/tui.md)、[Slash 命令](guide/commands.md)
- 多模型与池化：[模型与鉴权](guide/models.md)
- 会话分叉与回溯：[会话与分支](guide/sessions.md)
- 写自己的工具/扩展：[Bundle 开发](bundles/README.md)
