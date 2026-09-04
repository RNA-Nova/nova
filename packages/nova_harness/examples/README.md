# nova_harness 示例

本目录包含可直接运行的 Python 示例，覆盖 nova_harness 高阶 SDK 的核心能力。所有示例默认离线运行（01/02 经 `CreateAgentSessionOptions.model_runtime` 注入 mock 模型运行时，03 演示的扩展生命周期本身不需要模型）；真实 API 调用部分依赖环境变量中的 API Key，未设置时自动跳过。

## 环境要求

- Python >= 3.12, < 3.14
- 已安装 `nova_harness`（monorepo 内用 pixi dev 环境：`pixi install --environment dev`；或 `pip install nova-harness`，`nova-ai` / `nova-agent` 随依赖一并安装）

## 示例列表

| 文件 | 主题 |
|------|------|
| `01_quickstart.py` | 最小会话：`SessionManager.in_memory` + `create_agent_session`，一轮 prompt，打印回复与 token 用量，`dispose` |
| `02_events.py` | 事件流订阅：`session.subscribe()` 打印 agent / turn / message 生命周期与运行时事件序列，退订 |
| `03_extension.py` | 最小扩展：`session_start` 钩子 + 注册 slash 命令，演示扩展发现、装载与 `bind_extensions()` 生命周期 |

## 运行方式

```bash
cd packages/nova_harness
python examples/01_quickstart.py
```

每个脚本都是自包含的：会话与配置全部落在临时目录（内存态 SessionManager + 临时 `agent_dir`），不会读写 `~/.nova/agent` 下的任何文件。

## 真实 API 调用

01 / 02 中的真实调用默认使用 Volcengine（也可自行替换为其他已配置鉴权的 provider）：

```bash
export VOLCENGINE_API_KEY="<your-key>"
python examples/01_quickstart.py
```

> **注意**：请勿在示例中写入真实 API Key。所有 key 一律通过环境变量注入。
