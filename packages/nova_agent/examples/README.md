# nova_agent 示例

本目录包含可直接运行的 Python 示例，覆盖 `nova_agent` 的核心能力。**全部示例使用 mock stream_fn，离线即可运行，不需要任何 API Key。**

## 环境要求

- Python >= 3.9
- 已安装 `nova_agent` 与 `nova_ai`（pixi workspace 或 `pip install -e packages/nova_agent`）

## 示例列表

| 文件 | 主题 |
|------|------|
| `01_quickstart.py` | Agent 最小用法：创建、订阅事件、prompt、状态查看 |
| `02_custom_tools.py` | 继承 `AgentTool` 实现自定义工具；JSON Schema 校验与类型矫正（coercion） |
| `03_hooks.py` | 四个 hook：`before_tool_call` 拦截、`after_tool_call` 改写、`prepare_next_turn`、`should_stop_after_turn` |
| `04_steering_followup.py` | `steer()` 运行中插入、`follow_up()` 停止前排队、队列 drain 模式 |
| `05_abort_continue.py` | `abort()` 取消 run、`wait_for_idle()` 的 shield 语义、`continue_()` 继续 |
| `06_agent_loop_lowlevel.py` | 低层 facade：`agent_loop()` 返回 `AgentEventStream`，自管状态 |

另有两个辅助文件：

- `generate_event_flow_log.py`：生成各场景完整事件流日志的工具脚本（`python examples/generate_event_flow_log.py`）
- `EVENT_FLOW_LOG.md`：上述脚本的生成产物，事件序列参考

## 运行方式

```bash
cd packages/nova_agent
python examples/01_quickstart.py
```

## 接入真实模型

示例中的 mock `stream_fn` 可直接替换为真实调用：不传 `stream_fn` 时 Agent 默认使用 `builtin_models().stream_simple`（auth 从环境变量解析）：

```bash
export VOLCENGINE_API_KEY="<your-key>"
```

```python
from nova_ai import get_volcengine_model

agent = Agent()  # 默认 stream_fn 走内置 Models
agent.set_model(get_volcengine_model("deepseek-v3-2-251201"))
await agent.prompt("你好")
```

> **注意**：请勿在示例中写入真实 API Key，一律通过环境变量注入。
