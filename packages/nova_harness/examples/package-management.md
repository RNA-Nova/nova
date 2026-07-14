# Nova Python 包管理设计与使用指南

> 本文档面向 Nova 开发者与高级用户，说明 `nova_harness.core.package` 的设计路线、安装语义、目录布局与典型用法。

---

## 1. 设计哲学

Nova 的包管理走 **"Nova 包装 Python 包安装工具链，资源与 Python 包解耦"** 的路线：

1. **Nova 负责资源发现**：agent、tool、skill、extension、prompt 这些资源统一按 Nova 的目录约定或 `[tool.nova]` 清单识别。
2. **Nova 负责包目录管理**：所有包整体安装在 Nova 自己的目录（`~/.nova/agent/packages/` 或项目级 `.nova/agent/packages/`），保持包结构完整，不做传统意义上的 `agents/`、`tools/` 拆分复制。
3. **Python 依赖/包仍走标准 Python 工具链**：Nova 不重新实现依赖解析，而是调用 `pixi` / `uv` / `pip` 安装 Python 依赖，并在需要时以 `--no-deps` 方式安装包自身，供 tool executor 与 extension 共享 helper 模块。
4. **来源驱动**：每个安装包都有 `source`（path / git），settings 中记录 source，`PackageManager` 在运行时会调用内部的 `PackageResolver` 根据 source 重新解析到本地目录。

这样设计的好处：

- 包在文件系统中保持完整，便于调试、回滚、查看源码。
- 不破坏 Python 生态习惯：`pyproject.toml`、Poetry、PEP 621、`requirements.txt` 都原生支持。
- 工具与扩展可以 `import` 包内的共享模块，而不需要把 helper 代码复制到 Nova 框架内。

---

## 2. 核心模块职责

```
nova_harness.core.package
├── manager.py                # PackageManager：统一 facade（安装 + 资源解析）
├── coordinator.py            # PackageCoordinator：settings 与已安装包的协调/自动安装
├── installer.py              # PackageInstaller：安装/卸载/列表/信息/验证的薄 facade
├── install/
│   ├── operation.py          # InstallOperation：普通/editable 安装的共享实现
│   ├── lifecycle.py          # PackageLifecycle：更新/卸载
│   ├── query.py              # PackageQuery：列表/信息/验证
│   └── helpers.py            # install 子系统纯 helper 函数
├── scaffold.py               # 自动生成 [tool.nova] 段
├── fs.py                     # 文件系统辅助函数（copytree / safe_remove / now_iso 等）
├── backend/
│   ├── python.py             # pixi/uv/pip 后端：依赖与 Python 包安装
│   └── binaries.py           # 可选二进制依赖检测与 pixi global 安装
├── metadata/
│   ├── pyproject.py          # pyproject.toml / manifest / 依赖读取
│   ├── entries.py            # 收集包内 agents/tools/skills/extensions/prompts
│   └── validation.py         # 判断目录/文件是否为合法 agent/tool/extension
├── source/
│   ├── spec.py               # source 字符串解析（path/git/https）
│   └── fetcher.py            # git clone/update 与本地路径解析
└── resolver/                 # 运行时资源解析子系统（被 PackageManager 内部使用）
    ├── resolver.py           # PackageResolver：按优先级解析所有资源路径
    ├── discovery.py          # 本地目录资源扫描
    ├── patterns.py           # include/exclude/override 模式匹配
    └── metadata.py           # ResolvedResource / PathMetadata 构建
```

### install 子系统交互

```python
PackageInstaller
├── InstallOperation   # install() 的实际执行
├── PackageLifecycle   # update() / uninstall()
└── PackageQuery       # list() / info() / validate()
```

- `install()` 先由 `SourceResolver` 解析 source，`read_manifest` 读取 manifest，再交给 `InstallOperation.run()`。
- `install_and_persist()` 在 `install()` 之后调用 `SettingsManager.add_package_source()` 写入 settings。
- `update()` 通过 `PackageLifecycle` 重新执行安装并持久化 source。
- `uninstall()` 通过 `PackageLifecycle` 删除目录/symlink、卸载 Python 包、移除 settings 记录。
- `list()` / `info()` / `validate()` 通过 `PackageQuery` 查询，不修改文件系统。

### 资源解析协调

```python
PackageManager
├── PackageInstaller   # 安装/卸载/列表
├── PackageResolver    # 运行时资源路径解析
└── PackageCoordinator # 协调 settings、已安装包、自动安装兜底
```

`resolve_resources()` 的协调逻辑在 `PackageCoordinator` 中：

1. 从 global/project settings 收集配置的 package sources。
2. 如果启用 `install_missing_packages`，对解析不到的 source 自动调用对应 scope 的 `installer.install()`。
3. 最后调用 `PackageResolver.resolve()` 输出 `ResolvedPaths`。

---

## 3. Source 规范

`PackageManager.install(source)` 支持以下 source 格式：

| 格式 | 示例 | 说明 |
|------|------|------|
| 隐式 path | `/path/to/pkg` | 绝对路径 |
| 隐式 path | `./my-agent` | 相对路径 |
| 显式 path | `path:/path/to/pkg` | 同上 |
| editable | `path:/path/to/pkg` + `--editable` | 开发者模式：原地引用，不复制 |
| git | `git:github.com/user/repo@main` | 克隆到 Nova 缓存目录 |
| git | `git:git@github.com:user/repo.git@v1.0` | SSH 形式 |
| https | `https://github.com/user/repo` | 自动识别为 git 源 |
| https with ref | `https://github.com/user/repo@abc1234` | commit / tag / branch |

`get_package_identity(source)` 用于去重：

- `git:github.com/user/repo@main` 与 `git:github.com/user/repo@v1.0` 身份相同（去掉 ref）。
- `path:./pkg` 会规范化为绝对路径后的身份。
- 同一 path 源无论以普通模式还是 `--editable` 模式安装，身份相同，会互相覆盖。

---

## 4. 安装流程

`PackageManager.install(source)` 的执行顺序：

```
1. parse_source(source) -> PackageSource
2. SourceResolver.resolve(source) -> 本地绝对路径 abs_src
3. read_manifest(abs_src) -> PackageManifest
4. collect_all_package_entries(abs_src) -> agents/tools/skills/extensions/prompts
5. 判断是否需要安装 Python 包自身：
   install_python_package = (tools 或 extensions 存在) 且 pyproject 有 name
6. Phase 1: 安装/检查 Python 依赖
   - 读取 [tool.poetry.dependencies] / [project.dependencies] / requirements.txt
   - dry_run 时：pip install --dry-run
   - 正常安装时：先 check_dependency_conflicts，再 install_dependencies
7. Phase 2: 安装包自身到当前 Python 环境（仅 tools/extensions 包）
   - 普通 path/git 源：pip install <pkg> --no-deps
   - `--editable` 模式：pip install -e <pkg> --no-deps
8. Phase 3: 复制整个包到 Nova 管理目录（`--editable` 模式下创建符号链接）
9. 返回 PackageMetadata
```

`install_and_persist()` 与 `install()` 的唯一区别：前者会把 source 写入 settings，实现"装完即生效"。

---

## 5. 各种模式下的安装示例与目录

以下示例假设默认全局 `agent_dir = ~/.nova/agent`。

### 5.1 普通 path 源（bundle / agent / tool / extension）

```bash
nova-pkg install /path/to/nova_coding_agent
# 或
nova-pkg install path:/path/to/nova_coding_agent
```

包结构：

```
/path/to/nova_coding_agent
├── pyproject.toml
├── requirements.txt          # 可选
├── agents/
│   └── coding_agent/
│       ├── agent.yaml
│       ├── description.md
│       └── sections/
├── tools/
│   ├── bash/
│   │   ├── schema.json
│   │   └── executor.py
│   └── write/
│       ├── schema.json
│       └── executor.py
└── extensions/
    └── session_commands/
        └── extension.py
```

安装后：

```
~/.nova/agent/
├── packages/
│   └── path/
│       └── nova-coding-agent/          # 完整包副本
│           ├── pyproject.toml
│           ├── agents/
│           ├── tools/
│           └── extensions/
└── settings.json                       # 记录 source
```

同时：

- Python 依赖被安装到当前 Python 环境。
- 因为包包含 tools 和 extensions，且 `pyproject.toml` 声明了包名，`nova-coding-agent` 本身会被 `pip install /path/to/nova_coding_agent --no-deps` 安装到 Python 环境，方便 tool executor 共享 helper 模块。
- **同名 path 包后装覆盖先装**：同一 scope 内如果已经存在名为 `nova-coding-agent` 的包，再次安装同名 path 包会先卸载旧包，再写入新副本。

### 5.2 editable 源（开发者模式）

```bash
nova-pkg install path:/path/to/my-tool --editable
# 或
nova-pkg install ./my-tool --editable
```

安装后：

```
~/.nova/agent/
├── packages/
│   └── path/
│       └── my-tool/ -> /path/to/my-tool   # 目录 symlink，不复制内容
└── settings.json                            # 记录 { "source": "/absolute/path/to/my-tool", "editable": true }
```

特点：

- **不复制**包目录，而是在 `packages/path/<name>/` 下创建一个指向原始目录的 symlink。
- Python 依赖照常安装。
- 包自身以 `pip install -e /path/to/my-tool --no-deps` 安装为 editable。
- 修改原始目录立即生效，无需重新 install。
- `uninstall` 时只卸载 Python 包、移除 settings 记录和 symlink，不删除原始目录。

### 5.3 git 源

```bash
nova-pkg install git:github.com/liujinming/nova-coding-agent@main
```

安装后：

```
~/.nova/agent/
├── packages/
│   └── git/
│       └── github.com/
│           └── liujinming/
│               └── nova-coding-agent/    # git clone 缓存
│                   ├── .git
│                   ├── pyproject.toml
│                   ├── agents/
│                   └── tools/
└── settings.json                          # 记录 git:github.com/liujinming/nova-coding-agent@main
```

更新时：

```bash
nova-pkg update nova-coding-agent
```

会执行 `git fetch` / `git checkout <ref>` / `git pull`，然后重新走安装流程。

### 5.4 仅安装 agent（无 tools/extensions）

```bash
nova-pkg install /path/to/my-agent
```

包结构：

```
/path/to/my-agent
├── pyproject.toml
└── agents/
    └── my-agent/
        ├── description.md
        └── sections/
```

安装后：

```
~/.nova/agent/
├── packages/
│   └── path/
│       └── my-agent/
│           ├── pyproject.toml
│           └── agents/
└── settings.json
```

特点：

- 因为包内只有 agent，没有 tool/extension，所以**不会**把 `my-agent` 作为 Python 包安装到当前环境。
- 只有 `pyproject.toml` 中的 Python 依赖会被安装。

### 5.5 仅安装 tool

```bash
nova-pkg install /path/to/my-tool
```

包结构：

```
/path/to/my-tool
├── pyproject.toml          # name = "my-tool"
└── tools/
    └── my-tool/
        ├── schema.json
        └── executor.py
```

安装后：

```
~/.nova/agent/
├── packages/
│   └── path/
│       └── my-tool/
└── settings.json
```

Python 环境侧：

- 安装 `pyproject.toml` 声明的依赖。
- 因为包含 tool 且声明了包名，`my-tool` 会被 `pip install /path/to/my-tool --no-deps` 安装到 Python 环境。
- tool executor 可以通过 `import my_tool.tools_common.xxx` 使用共享 helper。

### 5.6 仅安装 extension

与 tool 类似：包含 extension 且声明了包名时，会安装 Python 包自身。

### 5.7 仅安装 skill

```bash
nova-pkg install /path/to/my-skills
```

包结构：

```
/path/to/my-skills
├── pyproject.toml
└── skills/
    ├── web-search/
    │   └── SKILL.md
    └── code-review/
        └── SKILL.md
```

安装后：

```
~/.nova/agent/
├── packages/
│   └── path/
│       └── my-skills/
└── settings.json
```

特点：

- skill 不触发 Python 包自身安装（除非同时包含 tools/extensions）。
- skill 是纯文本/标记资源，由 `PackageManager` 内部调用 `PackageResolver` 在运行时按 `SKILL.md` 发现。

### 5.8 不安装依赖

```bash
nova-pkg install /path/to/pkg --no-deps
```

跳过 Phase 1 的 Python 依赖安装，但仍会：

- 检查/提示可选二进制依赖。
- 安装包自身（如果包含 tools/extensions 且有包名）。
- 复制包到 Nova 目录。

### 5.9 预览安装

```bash
nova-pkg install /path/to/pkg --dry-run
```

不会真正修改文件系统或 Python 环境，仅输出：

```
[dry-run] Would install my-pkg with 1 agent(s), 2 tool(s), 0 skill(s), 1 extension(s) and Python package 'my-pkg'
```

---

## 6. 项目级 vs 全局级

`PackageManager` 支持两种作用域：

```python
from nova_harness.core.package import PackageManager

# 全局级（默认）
pm = PackageManager()
# agent_dir = ~/.nova/agent

# 项目级：在方法上指定 local=True
pm = PackageManager()
pm.install_and_persist("/path/to/pkg", local=True)
pm.list(local=True)
pm.uninstall("pkg", local=True)
```

- 全局级写入 `~/.nova/agent/settings.json`。
- 项目级写入 `<cwd>/.nova/settings.json`。
- `PackageManager.resolve_resources()` 解析时优先级：**project packages > user packages > project direct entries > user direct entries > project auto-discovery > user auto-discovery**。

---

## 7. Python 后端选择

`backend/python.py` 按以下优先级选择安装工具：

1. **pixi（项目感知）**：当前目录在 pixi 项目内（存在 `pixi.toml` 或 `pyproject.toml` 含 `[tool.pixi`）且 `pixi` 命令可用。
   - 使用 `pixi run python -m pip install ...`
   - 这样 Nova 安装不会污染项目 `pyproject.toml`，但仍处在 pixi 环境里。
2. **uv（默认首选）**：`uv` 命令可用且不在 pixi 项目内时。
   - 使用 `uv pip install --python <sys.executable> ...`
   - uv 是 Nova 的默认推荐后端：速度快、支持 lock 文件、兼容 pip 语义。
3. **pip（兜底）**：没有 uv/pixi 时。
   - 使用 `<sys.executable> -m pip install ...`

设计意图：**uv 是默认一等公民**，pixi 只在它自己管理的项目里优先（避免破坏项目环境），pip 保证无工具也能跑。

无论哪个后端，安装包自身时都会加 `--no-deps`，因为依赖已经在 Phase 1 单独处理。

---

## 8. Resolver：运行时资源解析

`PackageManager` 在运行时会调用内部的 `PackageResolver` 把各种来源的资源合并成 `ResolvedPaths`：

```
1. 自动发现（后写入，可被后续阶段覆盖 metadata）
   - USER:    ~/.nova/agent/extensions/、skills/、prompts/、tools/、agents/
   - PROJECT: <cwd>/.nova/extensions/、skills/、prompts/、tools/、agents/
2. settings 中的直接资源列表
   - global_settings.extensions / skills / prompts / agents
   - project_settings.extensions / skills / prompts / agents
3. settings 中配置的 packages（后写入，优先级最高）
   - user packages
   - project packages
   - 解析每个 package 的 [tool.nova]，按 filter 取子集
4. 排序输出
```

同一个资源路径在不同来源中出现时，**后写入的来源覆盖先写入来源的 metadata**；被排除的路径保留在结果中，但 `enabled=False`。

### 8.1 Package Filter

settings 中的 package source 可以带 filter：

```toml
[tool.nova-agent.settings.packages]
# 字符串形式
packages = ["git:github.com/user/repo@main"]

# 带 filter 的对象形式
packages = [
  { source = "git:github.com/user/repo@main", tools = ["bash", "write"], agents = [] },
]
```

filter 语义：

- `agents = ["agent-a"]`：只加载该 agent。
- `agents = []`：显式禁用所有 agent。
- `agents = null` 或未声明：加载全部。
- 支持 glob 与 override 语法：
  - `!pattern`：排除匹配路径
  - `+path`：强制包含
  - `-path`：强制排除

---

## 9. 包约定与 manifest

### 9.1 完整 Nova 包

一个最小 Nova 包：

```toml
# pyproject.toml
[tool.poetry]
name = "my-bundle"
version = "0.1.0"
description = "My Nova bundle"
authors = ["Nova"]

[tool.poetry.dependencies]
python = ">=3.9,<3.13"
requests = "^2.0"

[tool.nova]
agents = ["./agents/my-agent"]
tools = ["./tools/my-tool"]
skills = ["./skills/my-skill"]
extensions = ["./extensions/my-extension"]
auto_install_dependencies = true
```

### 9.2 无 `[tool.nova]` 的包

如果不写 `[tool.nova]`，Nova 会按标准目录名自动扫描：

- `agents/` 下每个含 `description.md` / `agent.yaml` / `sections/` 的目录。
- `tools/` 下每个含 `schema.json` / `executor.py` 的目录。
- `skills/` 下每个含 `SKILL.md` 的目录或文件。
- `extensions/` 下每个含 `extension.py` 的目录或文件。
- `prompts/` 下每个 `.md` 文件。

### 9.3 无 `pyproject.toml` 的包

**支持完全没有 `pyproject.toml` 的包**，但能力受限：

```
/path/to/my-agent
└── agents/
    └── my-agent/
        ├── description.md
        └── sections/
            └── role.md
```

行为：

- `name` 退化为目录名（`my-agent`）。
- `version` 固定为 `0.0.0`，`description` 与 `author` 为空。
- `[tool.nova]` 不存在，按标准目录名扫描资源。
- 如果存在 `requirements.txt`，仍然会被读取并安装依赖。
- **不会**把包自身安装为 Python 包（因为没有声明包名）。因此包含 `tools/` 或 `extensions/` 时，tool executor 无法 `import` 包内共享 helper 模块。

适用场景：纯 agent、纯 skill、不依赖共享 Python 模块的简单 tool。

使用 `nova-pkg init` 可以自动根据目录结构生成 `[tool.nova]` 段。

---

## 10. CLI 速查

```bash
# 安装
nova-pkg install /path/to/pkg
nova-pkg install /path/to/pkg --editable
nova-pkg install git:github.com/user/repo@main
nova-pkg install /path/to/pkg --no-deps
nova-pkg install /path/to/pkg --with-binaries
nova-pkg install /path/to/pkg --dry-run

# cd 到 pyproject.toml 同级目录，不带参数安装当前目录
cd /path/to/pkg
nova-pkg install
nova-pkg install --local        # 项目级安装
nova-pkg install --editable     # editable 模式

# 持久化安装（写入 settings）
nova-pkg install /path/to/pkg   # nova-pkg 默认持久化

# 更新
nova-pkg update my-bundle
nova-pkg update git:github.com/user/repo@main

# 卸载
nova-pkg uninstall my-bundle
nova-pkg uninstall /path/to/pkg

# 列表/信息
nova-pkg list
nova-pkg info my-bundle
nova-pkg validate /path/to/pkg

# 自动生成 [tool.nova]
nova-pkg init
```

---

## 11. 与 TypeScript 版的主要差异

TypeScript 版 `coding-agent` 的包管理是单文件 `core/package-manager.ts`，功能集中；Python 版 `core/package/` 按职责拆分为：

- `manager.py`：统一 facade，对外暴露 `PackageManager`。
- `coordinator.py`：`PackageCoordinator`，协调 settings、已安装包、resolver 与自动安装兜底。
- `installer.py`：薄 facade，组合 operation / lifecycle / query。
- `install/operation.py`：单次安装流程（普通/editable 共享 prepare + materialize）。
- `install/lifecycle.py`：update / uninstall。
- `install/query.py`：list / info / validate。
- `backend/python.py`：pixi/uv/pip 后端。
- `metadata/`：包元数据识别。
- `source/`：来源解析与获取。
- `resolver/`：运行时资源解析。

Python 版更强调：

1. **标准 Python 工具链**：直接调用 pixi/uv/pip，不重复造轮子。
2. **整包保留**：包目录完整复制到 Nova 目录，不拆散资源。
3. **source 驱动**：settings 中记录 source，resolver 按需重新解析。
4. **editable 开发者模式**：原生支持 `-e` 安装，方便本地开发。
