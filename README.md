# Nova

Nova 是一个用于构建大语言模型（LLM）智能体的 Python 单体仓库（monorepo）。

## 项目结构

```
nova/
├── packages/
│   ├── nova_ai/        # 统一的 LLM 提供商抽象层
│   ├── nova_agent/     # 事件驱动的异步 Agent 框架
│   ├── nova_harness/   # 高阶 Agent SDK（会话、压缩、工具链）
│   └── nova_team/      # 主从多智能体团队配置
├── README.md
├── CHANGELOG.md
└── LICENSE
```

## 子包简介

- **nova_ai**：支持流式调用、模型注册、多厂商鉴权的 LLM 统一封装。
- **nova_agent**（核心实现为 `nova_agent`）：提供 `Agent` 类、事件订阅、`agent_loop` 循环与生命周期管理。
- **nova_harness**：在底层框架之上构建 `AgentSession`，支持会话树、分支导航、上下文压缩与远程计算。
- **nova_team**：主从多智能体挂载配置与团队编排。

## 技术栈

- Python >= 3.9, < 3.13
- Poetry（各子包独立管理）
- `asyncio` / `mashumaro` / `dataclass`

## 安装与开发

每个子包都是独立的 Poetry 项目，进入对应目录后执行：

```bash
cd packages/nova_ai
poetry install

# 格式化
cd packages/<子包名>
poetry run black .
poetry run isort .
```

## 许可证

MIT License
