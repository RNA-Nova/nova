# ADR-006: 为什么 streaming/api.py 改名为 invoke.py

## 状态

已接受

## 背景

`streaming/` 目录下有一个文件提供面向用户的调用入口：

```python
def stream(model, context, options) -> AssistantMessageEventStream: ...
def complete(model, context, options) -> AssistantMessage: ...
def stream_simple(model, context, options) -> AssistantMessageEventStream: ...
def complete_simple(model, context, options) -> AssistantMessage: ...
```

这个文件最初命名为 `api.py`，后改为 `invoke.py`。

## 决策

改名为 `invoke.py`，保留 `api.py` 的语义会与其他地方的 `api` 产生混淆。

## 理由

### 1. `api` 在项目中有三种不同含义

| 地方 | `api` 的含义 |
|------|-------------|
| `types/enums.py` 里的 `Api` | API 协议类型枚举（openai-completions、anthropic-messages 等） |
| `api_impls/` | API 协议实现目录（OpenAI Completions、OpenAI Responses 等） |
| `streaming/api.py`（旧名） | "给用户用的函数层" |
| `model.api` | 模型使用的 API 协议类型 |

四个不同含义共用同一个词，阅读代码时容易混淆。

### 2. 文件的实际职责是"调用调度"

```python
def stream(model, context, options):
    adapter = resolve_api_adapter(model.api)   # 查找 adapter
    return adapter.stream(model, context, options)  # 分派调用
```

虽然这些函数是用户直接调用的入口，但内部逻辑是"根据 api 类型分派到对应 adapter"。`invoke` 比 `api` 更准确地描述了"调用"这个行为，同时避开了 `api` 的命名冲突。

### 3. 用户视角也成立

从用户视角看，`stream()` / `complete()` 是调用入口。`invoke`（调用/唤起）比泛指的 `api` 更精确地表达了这层语义。

## 替代方案

| 候选 | 评价 |
|------|------|
| `dispatch.py` | 强调"分派"，但用户不关心内部分派逻辑 |
| `caller.py` | 中性，也可以 |
| `client.py` | 不合适，这里不是 HTTP 客户端 |
| 保留 `api.py` | 可接受但不精确，存在命名冲突 |

最终选择 `invoke.py`，兼顾了内部实现语义和用户使用语义。

## 后果

- **正面**：命名冲突消除，文件职责更清晰
- **负面**：需要更新所有导入路径（已完成：158 个测试通过）

## 相关代码

- `src/nova_ai/streaming/invoke.py`
- `src/nova_ai/streaming/__init__.py`
- `src/nova_ai/__init__.py`
