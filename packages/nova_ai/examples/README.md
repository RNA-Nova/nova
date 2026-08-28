# nova_ai 示例

本目录包含可直接运行的 Python 示例，覆盖 nova_ai 的核心能力。所有示例默认离线运行（mock 协议实现），真实 API 调用部分依赖环境变量中的 API Key，未设置时自动跳过。

## 环境要求

- Python >= 3.9
- 已安装 `nova_ai`（pixi workspace 或 `pip install -e packages/nova_ai`）

## 示例列表

| 文件 | 主题 |
|------|------|
| `01_quickstart.py` | 最小用法：mock 协议模块 + `builtin_models()` 真实调用 |
| `02_stream_events.py` | 流式事件类型详解：text / thinking / toolcall 事件的消费顺序 |
| `03_models_and_providers.py` | Models 注册表：内置 provider、自定义 provider 注册、动态模型目录（`fetch_models`） |
| `04_auth.py` | Auth 解析链：环境变量、`options.api_key` 覆盖、动态 key 注入 |

## 运行方式

```bash
cd packages/nova_ai
python examples/01_quickstart.py
```

## 真实 API 调用

示例中的真实调用默认使用 Volcengine（也可自行替换为其他 provider）：

```bash
export VOLCENGINE_API_KEY="<your-key>"
python examples/01_quickstart.py
```

> **注意**：请勿在示例中写入真实 API Key。所有 key 一律通过环境变量注入。
