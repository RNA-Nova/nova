# nova-coding-agent

Nova 官方编程 Agent bundle，提供本地文件系统操作与命令执行能力。

## 安装

这是一个纯 Nova bundle，通过 `nova-pkg` 安装：

```bash
nova-pkg install /path/to/nova_coding_agent
```

安装时会自动读取 `pyproject.toml` 并安装本地 path 依赖（`nova-ai`、`nova-agent`、`nova-harness`）。

## 目录结构

```
nova_coding_agent/
├── agents/
│   └── coding_agent/      # Agent 配置
├── tools/
│   ├── bash/              # 执行 shell 命令
│   ├── edit/              # 批量文本替换
│   ├── find/              # 查找文件/目录
│   ├── grep/              # 搜索文件内容
│   ├── ls/                # 列出目录
│   ├── read/              # 读取文件/图片
│   └── write/             # 写入文件
├── package.json           # Nova bundle 清单
└── pyproject.toml         # Python 依赖声明
```

## 可选依赖

`grep` 与 `find` 工具优先使用外部二进制以获得更好性能：

- `rg`（ripgrep）
- `fd`（fd-find）

未安装时会自动回退到纯 Python 实现。如需安装：

```bash
# macOS
brew install ripgrep fd

# Debian/Ubuntu
sudo apt-get install ripgrep fd-find
```

安装 bundle 时加上 `--with-binaries` 可尝试自动安装（需要对应系统包管理器权限）：

```bash
nova-pkg install /path/to/nova_coding_agent --with-binaries
```

## 开发

```bash
cd /path/to/nova_coding_agent
poetry run black tools/
poetry run isort tools/
poetry run pytest
```
