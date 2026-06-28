# nova_harness

`nova_harness` 是 Nova monorepo 的高阶 Agent SDK，建立在 `nova_ai` + `nova_agent` 之上。

## 职责

- **AgentSession**：封装 `Agent`，提供自动重试、模型切换、会话持久化。
- **会话树管理**：分支（branch）、fork、导航与会话统计。
- **上下文压缩**：通过 LLM 生成摘要，自动或手动缩减 token 占用。
- **资源加载**：提示词模板、诊断与资源冲突检测。
- **设置持久化**：本地 JSON 存储用户设置与模型配置。
- **工具链**：内置工具的注册与运行时白名单控制。

## 安装

```bash
cd packages/nova_harness
poetry install
```

## 主要依赖

- `nova-ai`（本地路径依赖）
- `nova-agent`（本地路径依赖）
- `pydantic ^2.0`
- `json-repair >= 1.0`
- `pyyaml ^6.0`
- `filelock ^3.0`

## 运行测试

```bash
# 单元测试
PYTHONPATH=src:../nova_ai/src:../nova_agent/src python -m pytest tests/ -m "not integration"

# 集成测试（需配置 VOLCENGINE_API_KEY）
PYTHONPATH=src:../nova_ai/src:../nova_agent/src python -m pytest tests/ -m integration
```

## 示例

详见 [`examples/`](./examples) 目录下的 Jupyter Notebook：

- `01-quickstart.ipynb` — 创建 `AgentSession` 并发起对话
