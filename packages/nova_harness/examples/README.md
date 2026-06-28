# nova_harness 示例

本目录包含可直接在 Jupyter 中运行的 `nova_harness` 示例 Notebook。

## 环境要求

- Python >= 3.9
- 已安装 `nova_harness` 及其依赖（`nova_ai`、`nova_agent`）
- 配置 API Key（示例默认使用 Volcengine）：

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

## Notebook 列表

| 文件 | 主题 |
|------|------|
| `01-quickstart.ipynb` | 创建 `AgentSession`、发起对话、查看消息 |

## 运行方式

```bash
cd packages/nova_harness
jupyter lab examples/
```

按顺序执行单元格即可。
