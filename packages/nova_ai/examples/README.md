# nova_ai 示例

本目录包含可直接在 Jupyter 中运行的示例 Notebook，帮助理解 `nova_ai` 的公共 API。

## 环境要求

- Python >= 3.9
- 已安装 `nova_ai`：`cd packages/nova_ai && poetry install`
- 至少配置一个厂商的 API Key（示例默认使用 Volcengine）：

```bash
export VOLCENGINE_API_KEY="3b631f71-6bd6-464a-9abc-b0e8d19f25d7"
# 或
# export OPENAI_API_KEY=""  # 本地未设置，已注释
```

## Notebook 列表

| 文件 | 主题 |
|------|------|
| `01-quickstart.ipynb` | 最简流式对话：配置 Key、发起 `stream_simple`、消费事件、获取最终消息 |
| `02-multi-provider.ipynb` | 查看已注册 provider/model，并演示同一接口切换不同厂商模型 |
| `03-tools.ipynb` | 定义 `Tool`、让模型生成 `ToolCall`、本地执行、构造 `ToolResultMessage` 并继续对话 |

## 运行方式

```bash
cd packages/nova_ai
jupyter lab examples/
```

打开任意 Notebook 后按顺序执行单元格即可。需要真实 API Key 的 Notebook 在首个代码单元格中提示设置环境变量。
