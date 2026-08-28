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
# 推荐：仓库根目录 pixi 统一环境（一把装全部子包）
pixi install --environment dev

# 兼容：子包独立 Poetry
cd packages/nova-harness/backend
poetry install
```

## 主要依赖

- `nova-ai`（本地路径依赖）
- `nova-agent`（本地路径依赖）
- `pydantic ^2.0`
- `pyyaml ^6.0`
- `filelock ^3.0`
- `tomli ^2.0.1`（Python < 3.11）

## 运行测试

```bash
# 推荐：pixi 任务（仓库根目录）
pixi run -e dev test-harness

# 或在子包内直接跑（pixi 环境下）
pixi run -e dev pytest packages/nova-harness/backend/tests -m "not integration"
```

## 示例

详见 [`examples/`](./examples) 目录下的 Jupyter Notebook：

- `01-quickstart.ipynb` — 创建 `AgentSession` 并发起对话
