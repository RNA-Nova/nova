# CLI 参考

## `nova`（TUI 前端）

```bash
nova [message...]            # 启动 TUI；message 为启动后首条消息（@file 展开为文件文本）
```

| 旗标 | 说明 |
|------|------|
| `--version` | 前端版本 |
| `-c, --cwd <dir>` | 工作目录（缺省当前目录） |
| `-m, --model <ref>` | 初始模型（`provider/id`） |
| `-a, --agent <name>` | 初始角色 |
| `--continue` | 继续当前目录最近一次会话 |
| `-r, --resume` | 启动后打开会话选择器 |
| `--session <file\|id>` | 恢复指定会话（文件路径或会话 id） |
| `-n, --name <name>` | 设置会话名 |
| `--thinking <level>` | 思考级别（off/minimal/low/medium/high…按模型能力） |
| `--no-session` | 内存态会话（不落盘、不进会话列表） |

## `nova-server`（后端；二进制形态在 `runtime/`，pip 渠道等价物见下）

```bash
nova-server [rpc|run|pkg] ...   # 缺省 rpc
```

| 模式 | 用途 | pip 渠道对应物 |
|------|------|---------------|
| `rpc`（缺省） | JSON-RPC over stdio 服务器（前端 spawn 挂载） | `nova-harness-rpc` |
| `run` | print 一次性执行（脚本/CI/子代理自调） | `nova-harness run` |
| `pkg` | 包管理器 | `nova-pkg` |

三个入口共用 `--version`（报后端版本，与 `initialize` 握手同源）。

### `nova-server run` 旗标

| 旗标 | 说明 |
|------|------|
| `agent`（位置参，可选） | 角色名 |
| `--task <文本>` | 任务内容 |
| `--cwd <dir>` | 工作目录 |
| `--json` | JSONL 事件流输出 |
| `--trust` | 信任当前项目（加载 `.nova` 资源；headless 缺省不信任） |
| `--no-session` | 不落盘（临时会话） |
| `--skill <路径>` | 临时加载 skill（可重复，不持久化） |
| `--prompt-template <路径>` | 临时加载模板（可重复） |
| `--tools, -t <逗号名单>` | 工具白名单（SDK 硬闸的 CLI 投影） |
| `--exclude-tools, -xt <逗号名单>` | 工具排除集（在 `--tools` 之后应用） |

### `nova-server pkg` 子命令

`list` / `install <源> [--editable] [--local]` / `uninstall <名或源> [--local]` / `update <名或源>` / `info <名>` / `validate <路径>` / `init` / `--version`

## `install.sh`（安装器）

```bash
curl -fsSL <release>/install.sh | sh                  # 安装/升级（幂等）
curl -fsSL <release>/install.sh | sh -s -- uninstall  # 卸载
```

| 环境变量 | 说明 |
|---------|------|
| `NOVA_VERSION` | 钉版本（`v0.1.0` 或 `0.1.0`；缺省查 latest） |
| `NOVA_INSTALLER_RELEASES_BASE` | 发布源覆盖（支持 `file://` 本地演练） |
| `NOVA_INSTALL_DIR` | 安装根（缺省 `~/.nova/agent/install`） |
| `NOVA_BIN_DIR` | bin 目录（缺省 `~/.local/bin`） |
