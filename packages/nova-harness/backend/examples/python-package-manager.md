# Nova Python 包管理指南

> 本文档描述 `nova_harness` 当前的包管理系统，对应源码位于 `src/nova_harness/core/package/`。
>
> 关键设计：包是**资源集合**，不再区分 `agent` / `tool` / `skill` / `extension` / `bundle` 等 kind；每个包通过 `pyproject.toml` 的 `[tool.nova]` 段声明提供了哪些资源。

---

## 1. 核心概念

### 1.1 包 = 资源集合

一个 Nova 包是文件系统上的一个目录，包含：

- `pyproject.toml`：标准 Python 包元数据 + `[tool.nova]` 资源声明
- `agents/`、`tools/`、`skills/`、`extensions/`：资源目录（可选）
- 可选的 Python 依赖、README、测试等

包内可以同时包含多种资源。例如 `nova-coding-agent` 同时包含：

- 1 个 agent：`agents/coding_agent/`
- 7 个 tool：`tools/bash/`、`tools/read/` 等
- 1 个 extension：`extensions/session_commands/`

### 1.2 不再使用 `kind`

旧实现中每个包需要声明 `kind = "agent" | "tool" | "skill" | "extension" | "bundle"`。当前实现已移除该字段：

- `nova-pkg install` 不再接受 `--kind` 参数
- `nova-pkg init` 不再生成 `kind` 字段
- `validate()` 直接扫描包内资源并分别校验标记文件

---

## 2. 目录结构

### 2.1 用户级（全局）

```
~/.nova/agent/
├── settings.json          # 全局 settings，含 packages 列表
├── auth.json              # API key 等鉴权信息
├── sessions/              # 会话历史
├── packages/              # 整包安装目录
│   ├── git/               # git 来源包
│   │   └── github.com/
│   │       └── nova-team/
│   │           └── nova-coding-agent/
│   │               ├── pyproject.toml
│   │               ├── agents/
│   │               ├── tools/
│   │               ├── skills/
│   │               └── extensions/
│   └── path/              # path 来源包
│       └── my-local-tools/
│           ├── pyproject.toml
│           └── tools/
├── agents/                # 零散 agent 自动发现
├── skills/                # 零散 skill 自动发现
├── extensions/            # 零散 extension 自动发现
├── prompts/               # 零散 prompt 自动发现
└── themes/                # 零散 theme 自动发现（预留）
```

### 2.2 项目级

```
<cwd>/
└── .nova/
    ├── settings.json      # 项目级 settings
    ├── packages/          # 项目级整包安装目录（使用 nova-pkg --local 时）
    │   ├── git/
    │   └── path/
    ├── agents/            # 项目级零散 agent
    ├── skills/            # 项目级零散 skill
    ├── extensions/        # 项目级零散 extension
    └── prompts/           # 项目级零散 prompt
```

> 注意：与旧版不同，全局和项目级都没有 `tools/` 自动发现目录。tool 通常依赖 Python 包，必须通过包安装保证依赖正确。
>
> 使用 `nova-pkg --local` 时，包会安装到 `<cwd>/.nova/agent/packages/`，source 记录到 `<cwd>/.nova/settings.json` 的 `packages` 字段。

---

## 3. 包 Manifest

Nova 包在 `pyproject.toml` 中通过 `[tool.nova]` 段声明资源：

```toml
[tool.poetry]
name = "my-nova-package"
version = "1.0.0"
description = "My Nova package"
authors = ["nova"]

[tool.poetry.dependencies]
python = ">=3.9,<3.13"

[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.nova]
agents = ["./agents/coding_agent"]
tools = ["./tools/bash", "./tools/read"]
skills = ["./skills/code-review"]
extensions = ["./extensions/session_commands"]
auto_install_dependencies = true
```

### 3.1 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `agents` | `List[str]` | agent 配置目录或文件路径 |
| `tools` | `List[str]` | tool 目录路径 |
| `skills` | `List[str]` | skill 目录或 `SKILL.md` 文件路径 |
| `extensions` | `List[str]` | extension 目录或 `extension.py` 文件路径 |
| `prompts` | `List[str]` | prompt template 路径（预留） |
| `auto_install_dependencies` | `bool` | 是否自动安装 Python 依赖，默认 `true` |

### 3.2 路径规则

- 相对路径：相对于 `pyproject.toml` 所在目录
- 显式空列表 `tools = []`：禁用该资源类型的目录扫描
- 省略字段：按约定目录自动扫描（`agents/`、`tools/`、`skills/`、`extensions/`）

### 3.3 标记文件

资源校验时检查以下标记：

| 资源 | 标记 |
|---|---|
| agent | `agent.yaml` / `description.md` / `setup.md` / `sections/` |
| tool | `executor.py`（`Tool` 类，元数据为类属性） |
| user tool | `executor.py`（`UserTool` 类） |
| skill | `SKILL.md` |
| extension | `extension.py` / `__init__.py` |

---

## 4. Source 类型与安装行为

### 4.1 path（本地路径）

**格式：**

```
/absolute/path
./relative/path
path:/absolute/path
path:./relative/path
```

**行为：**

1. 解析为 `PackageSource(type="path", spec=..., editable=False)`
2. 复制整个包到 `~/.nova/agent/packages/path/<name>/`
3. 如果包包含 `tools` 或 `extensions` 且声明了 Python 包名，会以 `--no-deps` 方式安装该包自身，供 tool executor / extension 共享 helper 模块（详见第 8 节 FAQ）

### 4.2 editable（原地引用 / 开发者模式）

**格式：**

```
path:/absolute/path
path:./relative/path
```

配合 `--editable`（或 `-e`）标志使用：

```bash
nova-pkg install path:/absolute/path --editable
nova-pkg install ./relative/path --editable
```

**行为：**

- 不复制文件，直接引用原始路径
- `install_path` 是 `packages/path/<name>/` 下的符号链接，指向原始目录
- 仍然会自动安装 Python 依赖（除非 `--no-deps`）
- 如果包包含 `tools` 或 `extensions` 且声明了 Python 包名，会以 **editable 方式**（`pip install -e <dir> --no-deps`）把包自身注册到 Python 环境，tool executor / extension 仍能 `from my_package.tools_common import ...`
- 适合本地开发调试，修改代码立即生效
- 卸载时只解除 Nova 引用和 Python 包注册，**不会删除原始目录**

### 4.3 git

**格式：**

```
git:github.com/user/repo
git:github.com/user/repo@v1.0.0
https://github.com/user/repo
https://github.com/user/repo@branch-name
```

**行为：**

1. 解析为 `PackageSource(type="git", host, repo_path, ref?)`
2. 克隆到 `~/.nova/agent/packages/git/<host>/<repo_path>/`
3. 如果有 `ref`，checkout 到对应分支 / tag / commit
4. 安装 Python 依赖和二进制依赖（按配置）

### 4.4 来源去重

同 identity 的 git 包只保留一个 settings 条目：

- `git:github.com/user/repo@main`
- `git:github.com/user/repo@v1.0`

会被视为同一个包，后安装的 source 覆盖前者。

---

## 5. CLI 用法

### 5.1 安装

```bash
# 本地路径
nova-pkg install ./my-package

# 可编辑安装（原地引用）
nova-pkg install ./my-package --editable

# git 来源
nova-pkg install git:github.com/nova-team/nova-coding-agent@v1.0.0

# 指定名称
nova-pkg install ./my-package --name my-name

# 跳过依赖
nova-pkg install ./my-package --no-deps

# 同时安装声明的二进制依赖（通过 pixi）
nova-pkg install ./my-package --with-binaries
```

### 5.2 卸载 / 更新 / 信息

```bash
nova-pkg uninstall my-package
nova-pkg update my-package
nova-pkg info my-package
```

### 5.3 列出

```bash
nova-pkg list          # 按包分组展示资源
nova-pkg list --flat   # 平铺列表
```

### 5.4 验证

```bash
nova-pkg validate ./my-package
```

### 5.5 初始化

```bash
nova-pkg init [directory]
```

根据当前目录结构自动生成 `[tool.nova]` 段。

---

## 6. 完整示例

### 6.1 最小 tool 包

```
my-tool/
├── pyproject.toml
└── tools/
    └── my-tool/
        ├── schema.json
        └── executor.py
```

`pyproject.toml`：

```toml
[tool.poetry]
name = "my-tool"
version = "1.0.0"
authors = ["nova"]

[tool.nova]
tools = ["./tools/my-tool"]
```

### 6.2 最小 agent 包

```
my-agent/
├── pyproject.toml
└── agents/
    └── my-agent/
        ├── description.md
        ├── setup.md
        └── sections/
            └── role.md
```

`pyproject.toml`：

```toml
[tool.poetry]
name = "my-agent"
version = "1.0.0"
authors = ["nova"]

[tool.nova]
agents = ["./agents/my-agent"]
```

### 6.3 混合包

```
my-bundle/
├── pyproject.toml
├── agents/
│   └── coding_agent/
├── tools/
│   ├── bash/
│   └── read/
└── extensions/
    └── session_commands/
```

`pyproject.toml`：

```toml
[tool.poetry]
name = "my-bundle"
version = "1.0.0"
authors = ["nova"]

[tool.nova]
agents = ["./agents/coding_agent"]
tools = ["./tools/bash", "./tools/read"]
extensions = ["./extensions/session_commands"]
auto_install_dependencies = true
```

---

## 7. Settings 持久化

安装并通过 `nova-pkg install` 持久化的包会写入 settings：

```json
{
  "packages": [
    "/absolute/path/to/my-package",
    { "source": "/absolute/path/to/dev-package", "editable": true },
    "git:github.com/nova-team/nova-coding-agent@v1.0.0"
  ]
}
```

- 全局位置：`~/.nova/agent/settings.json`，包安装到 `~/.nova/agent/packages/`
- 项目级位置：`<cwd>/.nova/settings.json`，包安装到 `<cwd>/.nova/agent/packages/`（使用 `nova-pkg --local` 时）

---

## 8. 常见问题（FAQ）

### Q1：`--no-deps` 是什么意思？

在 Python 包管理语境里，`--no-deps`（no dependencies）表示**只安装目标包本身，不自动安装它声明的 Python 依赖**。

Nova 里有两种场景会碰到 `--no-deps`：

1. **用户显式传 `--no-deps`**：`nova-pkg install ./pkg --no-deps` 会跳过 Phase 1 的依赖安装。
2. **内部自安装**：Phase 3 调用 `pip install <package_dir> --no-deps`，把 Nova 包自身装到当前 Python 环境。

第二种情况的 `--no-deps` 是为了**避免重复安装**——真正的第三方依赖已经在 Phase 1 装好了，Phase 3 只是把包目录本身注册到 `sys.path`，让包内模块可以被 `import`。

### Q2：为什么只有 tools 会触发 `--no-deps` 自安装？

因为 **tool 是唯一需要把自己当成 Python 包来 import 的资源**。

- **agents**：纯配置（`description.md`、`setup.md`、`agent.yaml`），不需要 import。
- **skills**：纯 markdown 文件，不需要 import。
- **extensions**：也是通过 `importlib` 动态加载单个 `.py` 文件（见 `core/extensions/loader.py`），当前实现没有把它们所属包安装到 Python 环境，所以扩展内如果写了 `from my_package.ext_common import ...` 会找不到模块。
- **tools**：`executor.py` 同样被动态加载，但很多 tool 需要共享同包内的 helper 模块（例如 `nova_coding_agent.tools_common`）。

因此当包声明了 tools 且有 Python 包名时，Nova 会执行：

```bash
pip install <package_dir> --no-deps
```

这样 `executor.py` 里就能正常 `from <package_name>.some_module import ...`。

### Q3：我已经把整个包装到 Python 环境了，为什么还需要 `--no-deps` 自安装？

**“复制到 `~/.nova/agent/packages/`” ≠ “安装到 Python 环境”**。

Phase 4 只是把包目录复制到 Nova 的管理目录，Python 解释器并不知道这个目录存在。如果 tool executor 写了：

```python
from my_package.tools_common import shared_helper
```

Python 会按 `sys.path` 查找 `my_package`。只有执行了 `pip install <package_dir>` 后，`my_package` 才会被注册到当前环境的 site-packages（或生成 `.pth` 链接），`import` 才能成功。

`--no-deps` 只是告诉 pip：

> “别管 `pyproject.toml` 里的依赖，那些我 Phase 1 已经处理过了；你现在只需要把这个包目录本身变成可 import 的。”

### Q4：skills 包需要 `--no-deps` 自安装吗？

**不需要**。skills 是 markdown 文档，Nova 直接读取文件内容即可，没有任何 Python import 需求，所以不会触发 Phase 3 的自安装。

### Q5：extensions 包也会自安装吗？

**会。** extensions 和 tools 采用相同的动态文件加载方式（`core/extensions/loader.py`），所以包管理器对两者一视同仁：只要包包含 `extensions` 且声明了 Python 包名，就会触发 `--no-deps` 自安装。

这意味着 extension 里也可以写：

```python
from my_package.ext_common import helper
```

### Q6：editable 模式和普通 path 安装对 tool import 的处理有区别吗？

**没有区别**，两者都会保证 tool 可以 import 到同包内的 helper 模块。

- 普通 path：`pip install <package_dir> --no-deps`
- editable：`pip install -e <package_dir> --no-deps`

editable 模式只是用“原地链接”代替“复制到 `packages/path/`”，其他流程和普通安装一致：依赖会装、tool 包自身也会注册到 Python 环境。

---

## 9. 与 TypeScript 侧的对应关系

| 维度 | Python `nova_harness` | TypeScript `coding-agent` |
|---|---|---|
| manifest 文件 | `pyproject.toml` 的 `[tool.nova]` | `package.json` 的 `"pi"` |
| 资源类型 | agents / tools / skills / extensions / prompts | extensions / skills / prompts / themes |
| 安装模型 | 整包保留在 `packages/{git,path}/` | 整包保留在 `git/` / `npm/` |
| npm 支持 | ❌ 未实现 | ✅ 原生支持 |
| 本地安装 | path 复制 / editable 原地引用 | 原地引用 |
| kind 概念 | ❌ 已移除 | ❌ 从未引入 |

---

## 10. 最佳实践

1. **优先使用标准子目录**：把 agent/tool/skill/extension 分别放到 `agents/`、`tools/`、`skills/`、`extensions/`，这样 `nova-pkg init` 可以自动扫描
2. **混合资源放同一个包**：一个包可以同时提供 agent + tool + extension，无需拆成多个包
3. **tool / extension 依赖 helper 模块**：在 `pyproject.toml` 中声明 Python 包名，tool executor 和 extension 都可通过 `from my_package.xxx import ...` 共享代码
4. **本地开发用 editable**：`nova-pkg install ./my-package --editable`。现在 editable 模式也会自动处理依赖和以 editable 方式注册 Python 包，开发体验和普通安装一致，只是不复制文件
5. **不要手动放 tool 到 `~/.nova/agent/tools/`**：该目录已取消，tool 必须通过包安装
