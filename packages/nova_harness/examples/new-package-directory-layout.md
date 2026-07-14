# 新版包管理目录结构

> 对应重构：Python 包管理从"打散复制"改为"整包安装"。

## 顶层目录

```
~/.nova/agent/
├── settings.json          # 用户级 settings，含 packages 列表
├── auth.json              # API key 等鉴权信息
├── sessions/              # 会话历史
├── packages/              # 整包安装目录（唯一安装目标）
├── agents/                # 零散 agent 自动发现
├── skills/                # 零散 skill 自动发现
├── extensions/            # 零散 extension 自动发现
├── prompts/               # 零散 prompt 自动发现
└── themes/                # 零散 theme 自动发现
```

## 消失的目录/文件

| 旧版 | 新版 | 原因 |
|---|---|---|
| `~/.nova/agent/tools/` | 完全取消 | tool 通常有 Python 依赖，必须通过包安装保证依赖正确 |
| `~/.nova/agent/packages.json` | 完全取消 | 包列表已合并到 `settings.json` 的 `packages` 字段 |
| 包内资源被打散复制到 `agents/`/`tools/`/`skills/`/`extensions/` | 取消 | 资源保留在包内，保持包完整性 |

## `packages/` 内部

```
packages/
├── git/                                    # git 来源包
│   └── github.com/
│       └── nova-team/
│           └── nova-coding-agent/
│               ├── .git/
│               ├── pyproject.toml          # Poetry 元数据 + [tool.nova] manifest
│               ├── agents/
│               │   └── coding_agent/
│               │       ├── agent.yaml
│               │       ├── description.md
│               │       └── sections/
│               ├── tools/
│               │   ├── bash/
│               │   │   ├── schema.json
│               │   │   └── executor.py
│               │   ├── read/
│               │   └── write/
│               ├── skills/
│               │   └── code-review/
│               │       └── SKILL.md
│               └── extensions/
│                   └── session_commands/
│                       └── extension.py
│
└── local/                                  # local 来源包
    └── my-local-tools/
        ├── pyproject.toml
        └── tools/
            └── my-tool/
                ├── schema.json
                └── executor.py
```

## 项目级目录

```
<cwd>/
└── .nova/
    ├── settings.json          # 项目级 settings
    ├── packages/              # 项目级整包安装目录（使用 nova-pkg --local 时）
    │   ├── git/
    │   └── path/
    ├── agents/                # 项目级零散 agent
    ├── skills/                # 项目级零散 skill
    ├── extensions/            # 项目级零散 extension
    ├── prompts/               # 项目级零散 prompt
    └── themes/                # 项目级零散 theme

    # 注意：没有 tools/ 目录
```

## `settings.json` 示例

```json
{
  "default_model": "openai/gpt-4o",
  "packages": [
    "git:github.com/nova-team/nova-coding-agent",
    "local:~/dev/my-local-tools"
  ],
  "extensions": [],
  "skills": [],
  "prompts": [],
  "themes": [],
  "agents": []
}
```

## `agent.yaml` 示例

```yaml
name: coding_agent
description: A coding assistant
model: openai/gpt-4o

tools:
  - bash
  - read
  - write
  - grep

skills:
  - code-review

extensions:
  - session_commands

subagents:
  - reviewer
```

## 关键变化说明

### 1. 包是唯一的安装单位

所有通过 `nova-pkg install` 安装的包都完整保留在 `packages/{git,local}/<identity>/` 下。资源不再被拆散复制出去。

### 2. tool 只能通过包发现

```
旧版：~/.nova/agent/tools/bash/
新版：~/.nova/agent/packages/git/github.com/nova-team/nova-coding-agent/tools/bash/
```

`agent.yaml` 里写 `- bash`，resolver 会在所有已安装包中查找 `tools/bash/`。

### 3. 自动发现目录只保留自包含资源

| 目录 | 是否保留 | 理由 |
|---|---|---|
| `agents/` | ✅ | agent 是配置文件，无外部依赖 |
| `skills/` | ✅ | skill 是自包含 markdown |
| `extensions/` | ✅ | extension 是自包含代码；如有依赖，应通过包安装 |
| `prompts/` | ✅ | prompt 是自包含 markdown |
| `themes/` | ✅ | theme 是自包含 json |
| `tools/` | ❌ | tool 通常依赖 Python 包，复制后无法保证依赖 |

### 4. 没有 `packages.json`

包 source 列表直接存放在 `settings.json` 的 `packages` 字段中。包的其他元数据（name、version、description、kind）从 `pyproject.toml` 读取；安装路径从 source 推导。
