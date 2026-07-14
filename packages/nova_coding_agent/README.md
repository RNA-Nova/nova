# nova-coding-agent

Nova 官方编程 Agent bundle，提供本地文件系统操作与命令执行能力。

## 安装

这是一个 Nova bundle，同时也是一个 Python 包，通过 `nova-pkg` 安装：

```bash
nova-pkg install /path/to/nova_coding_agent
```

安装时会自动读取 `pyproject.toml` 并安装本地 path 依赖（`nova-ai`、`nova-agent`、`nova-harness`），同时把 bundle 自身作为 Python 包装入环境，使 tools 可以 `import nova_coding_agent.tools_common`。

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
├── nova_coding_agent/     # bundle 自身的 Python 包
│   └── tools_common/      # tools 共享辅助模块
├── package.json           # Nova bundle 清单
└── pyproject.toml         # Python 包与依赖声明
```

## 可选依赖

`grep` 与 `find` 工具优先使用外部二进制以获得更好性能（如 `rg`、`fd`）。未安装时会自动回退到纯 Python 实现。可手动安装：

```bash
# macOS
brew install ripgrep fd

# Debian/Ubuntu
sudo apt-get install ripgrep fd-find
```

## 开发

```bash
cd /path/to/nova_coding_agent
poetry run black tools/
poetry run isort tools/
poetry run pytest
```
