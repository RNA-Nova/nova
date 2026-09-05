# 包清单：`[tool.nova]` 全字段

A 型包的身份证是 `pyproject.toml`：Poetry 段（`[tool.poetry]`）给元数据，`[tool.nova]` 段声明 Nova 资源。B 型包用 `package.json`（顶层 `nova` 键，只支持 `requires`）。

## 完整示例（取自官方 nova-coding-agent，删节版）

```toml
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"

[tool.poetry]
name = "nova-coding-agent"
version = "1.0.0"
description = "Nova coding agent bundle"
authors = ["you <you@example.com>"]

[tool.poetry.dependencies]
python = ">=3.12,<3.14"
# 包的 Python 依赖（安装时随包装配）
json-repair = ">=3"

[tool.nova]
# 包间依赖：被依赖的 nova 包不在即拒绝安装；卸载时被引用即拒绝卸载
requires = ["nova-base"]

# 七类能力资源（全部可选）：路径相对包根
agents = ["./agents/"]                       # 目录：扫描其下 *.yaml
tools = ["./backend/tools/bash.py", "./backend/tools/read.py"]
extensions = ["./backend/extensions/permission_gate.py"]
user_tools = ["./backend/user_tools/bash.py"]
prompts = ["./backend/prompts/debug.md"]
personas = ["./backend/personas/"]           # 目录：递归收 .md
skills = ["./backend/skills/"]               # 目录：递归收 SKILL.md

# 依赖装配
auto_install_dependencies = true             # pip 依赖自动装（pyproject 声明的）
binary_dependencies = { rg = "ripgrep" }     # PyPI wheel 可装的二进制（建议 pin 版本）
binary_managed_dependencies = ["fd"]         # 框架注册表托管的二进制
binary_system_dependencies = ["docker"]      # 只校验存在性的系统二进制（缺失仅警告）
```

## 字段语义

### `requires`（包间依赖）

- 安装时校验：被依赖包在 user/project 合并视图任一 scope 命中即满足，缺失即拒绝并附安装提示；
- 卸载时守护：被其他已装包 `requires` 引用的包拒绝卸载；
- v1 只做约束校验，不做来源解析（无中心 registry）；
- B 型包在 `package.json` 顶层声明：`"nova": { "requires": ["nova-base"] }`。

### 资源类目（七类）

| 类目 | 指向 | 形态 |
|------|------|------|
| `agents` | 组合声明目录/文件 | `*.yaml` |
| `tools` | LLM 工具 | 单文件 `.py` 或目录（`executor.py`） |
| `extensions` | 扩展 | 同工具纪律；目录形态收根级 `.py` 不递归 |
| `user_tools` | 用户工具（人类触发，如 `!` bash） | 同工具纪律 |
| `prompts` | 用户模板 | `.md`（`$@` 占位符） |
| `personas` | 人格文本 | 目录递归收 `.md`，命名 = 相对路径去后缀 |
| `skills` | 技能目录 | 递归收 `SKILL.md` |

### 名单三态（agent yaml 的能力名单字段共用此语义）

- **键缺席** = 全放不设防；
- **显式空列表 `[]`** = 全禁；
- **非空** = 名单生效（支持 `!` 排除）。

### 二进制依赖三族

| 族 | 适用 | 安装行为 |
|----|------|---------|
| `binary_dependencies` | PyPI 有平台 wheel（如 `ripgrep`） | 随 pip 依赖进环境 `bin/` |
| `binary_managed_dependencies` | PyPI 覆盖不了的官方必需（当前仅 `fd`） | 框架按注册表 pin 版本 + sha256 下载到 `~/.nova/agent/bin/` |
| `binary_system_dependencies` | 无自动安装渠道（docker 类守护进程） | 只校验存在性，缺失警告附安装指引，不代装 |

运行时经 `resolve_binary()` 三级解析：env bin → nova bin → PATH（托管优先；识别发行版别名如 `fdfind`）。**消费端纪律：二进制加速 + 纯 Python 兜底**——缺失不影响可用性。

### `auto_install_dependencies`

`true` 时安装期自动装 `pyproject.toml` 声明的 Python 依赖（uv 优先、pip 兜底；**冻结二进制形态**落到 `~/.nova/agent/packages/.site/`）。包自身若是可安装 Python 包（有 name + build-system），pip 渠道会以 `--no-deps` 自安装供 `import 包名` 使用；冻结形态由 `sys.path` 挂载替代（无需自安装）。

## settings 中的包条目（安装后的登记形态）

```json
{
  "packages": [
    "path:/abs/or/relative/bundle",
    { "source": "git:github.com/user/repo@main", "editable": false,
      "tools": ["bash", "read"], "autoload": true }
  ]
}
```

- 字符串或 dict；dict 可带七类资源的**过滤器**（只加载该包的部分资源）与 `editable`；
- project 级条目支持 `autoload: false`——在 user 层自动加载基础上局部翻转（`+path` 启用、`-path`/`!pattern` 禁用）；
- path 源按相对路径持久化（相对 settings 文件所在目录），settings.json 可随团队仓库共享。

## 校验与脚手架

```bash
nova-pkg validate ./my-bundle     # 安装前校验（目录合法性 + manifest）
nova-pkg init                     # 按当前目录结构生成 [tool.nova] 段
```
