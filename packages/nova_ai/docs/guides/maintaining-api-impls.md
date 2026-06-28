# 维护手册：添加新的 API 协议实现

本文档说明如何在 `nova_ai` 中添加新的 LLM API 协议实现。

---

## 目录结构

```
api_impls/
├── __init__.py
└── openai_completions.py          # 现有实现（参考范本）
```

新增协议实现时，在 `api_impls/` 下创建新文件，如 `anthropic_messages.py`。

---

## 实现步骤

### 第 1 步：实现流式处理函数

每个协议实现至少提供两个函数：

```python
async def stream_xxx(
    model: Model,
    context: Context,
    options: Optional[XxxOptions] = None
) -> AssistantMessageEventStream:
    """流式处理主函数"""
    stream = AssistantMessageEventStream()
    
    async def process_stream():
        output = AssistantMessage(...)
        
        try:
            # 1. 建立连接 / 创建客户端
            # 2. 转换消息格式（先用 utils.transform_messages 做跨模型转换）
            # 3. 发送请求并处理流式响应
            # 4. push 事件到 stream
            
            stream.push(DoneEvent(reason=StopReason.STOP, message=deepcopy(output)))
            stream.end()
            
        except Exception as e:
            output.stop_reason = StopReason.ERROR
            output.error_message = str(e)
            stream.push(ErrorEvent(reason=StopReason.ERROR, error=deepcopy(output)))
            stream.end()
    
    asyncio.create_task(process_stream())
    return stream


def stream_simple_xxx(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """简化流式处理"""
    # 构建协议特定选项后调用主函数
    xxx_options = XxxOptions(...)
    return stream_xxx(model, context, xxx_options)
```

### 第 2 步：定义协议特定选项类

继承 `StreamOptions` 添加协议特有参数：

```python
class XxxOptions(StreamOptions):
    """XXX 协议特定选项"""
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    reasoning_effort: Optional[str] = None
```

### 第 3 步：更新 `api_impls/__init__.py`

新增协议需要在 `api_impls/__init__.py` 中导出，并更新 `ProviderStreamOptions` 联合类型：

```python
from .xxx import stream_xxx, stream_simple_xxx, XxxOptions

ProviderStreamOptions = Union[OpenAICompletionsOptions, XxxOptions]

__all__ = [
    "stream_openai_completions",
    "stream_simple_openai_completions",
    "OpenAICompletionsOptions",
    "stream_xxx",
    "stream_simple_xxx",
    "XxxOptions",
    "ProviderStreamOptions",
]
```

### 第 4 步：定义 Adapter 类并注册

在协议实现文件中定义一个实现 `ApiAdapter` Protocol 的类：

```python
class XxxAdapter:
    """XXX 协议适配器"""

    api = KnownApi.XXX_PROTOCOL  # 使用 enums 中定义的枚举值

    def stream(
        self,
        model: Model,
        context: Context,
        options: Optional[XxxOptions] = None
    ) -> AssistantMessageEventStream:
        return stream_xxx(model, context, options)

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: Optional[SimpleStreamOptions] = None
    ) -> AssistantMessageEventStream:
        return stream_simple_xxx(model, context, options)
```

然后在 `registry/builtins.py` 中注册该类的实例：

```python
def register_builtin_api_adapters() -> None:
    from ..api_impls.xxx import XxxAdapter

    register_api_adapter(XxxAdapter())
```

如果 `KnownApi` 枚举中没有对应值，先在 `types/enums.py` 中添加：

```python
class KnownApi(str, Enum):
    OPENAI_COMPLETIONS = "openai-completions"
    XXX_PROTOCOL = "xxx-protocol"
```

---

## 兼容性处理

如果新协议有第三方兼容层（如 OpenAI Completions 的众多第三方提供商），兼容性检测放在协议实现文件内部：

```python
def detect_compat(model: Model) -> XxxCompat:
    """从 provider 和 base_url 检测兼容性设置"""
    provider = model.provider
    base_url = model.base_url
    
    is_provider_a = provider == "xxx" or "xxx.com" in base_url
    
    return XxxCompat(
        supports_feature_x=not is_provider_a,
        requires_workaround_y=is_provider_a,
    )
```

**注意**：兼容性检测是协议实现特有的。OpenAI Completions 生态有大量第三方兼容服务，所以 `detect_compat` 特别复杂。新协议如果没有这种生态复杂度，不需要同等规模的兼容性检测。

兼容性配置类型放在 `types/compat.py` 中定义（见 [maintaining-types.md](./maintaining-types.md)）。

---

## 流式解析模式

参考 `openai_completions.py` 中的块管理闭包模式：

```python
async for chunk in response_stream:
    # 维护当前 content block 状态
    # 块类型变化时完成旧块并 push 事件
    # 更新 output.content 列表
```

核心模式：

1. **`ensure_text_block()`** —— 确保当前在构建 text 块
2. **`ensure_thinking_block()`** —— 确保当前在构建 thinking 块
3. **`ensure_tool_call_block()`** —— 确保当前在构建 toolCall 块
4. **`finish_block(block)`** —— 完成当前块并 push 结束事件

---

## 取消信号处理

如果协议实现底层 SDK 支持 `signal` 参数（如 TypeScript 的 OpenAI SDK），优先把 `options.signal` 传给 SDK，让它在 abort 时关闭连接。

Python OpenAI SDK 2.x 不直接支持 `signal`，则按以下方式模拟：

1. 请求发送前检查 `signal.aborted`，若已触发则直接抛 `"Request was aborted"`
2. chunk 循环内每次迭代前检查 `signal.aborted`，触发时主动关闭流并 break
3. break 后仍调用 `finish_block()` 收尾已有 block
4. 最后抛异常进入 `except` 路径，推送 `ErrorEvent(reason=StopReason.ABORTED)`

```python
signal = options.signal if options else None
if signal and getattr(signal, "aborted", False):
    raise Exception("Request was aborted")

async for chunk in openai_stream:
    if signal and getattr(signal, "aborted", False):
        await openai_stream.close()
        break
    # ... 处理 chunk

for block in blocks:
    finish_block(block)

if signal and getattr(signal, "aborted", False):
    raise Exception("Request was aborted")
```

**注意**：与"等流自然结束再标记 aborted"相比，主动断流可能丢失 abort 之后尚未到达的 chunk，但已产生的内容块必须正常收尾，保证事件序列完整。

---

## 测试要求

新增协议实现后，需要验证：

1. **导入测试** —— `tests/test_imports.py` 中确认新函数可导入
2. **基础功能测试** —— 至少测试消息转换和参数构建
3. **流式事件测试** —— 验证事件序列正确（Start → Text/Delta/End → Done）
4. **取消测试** —— 验证 abort 后流被关闭且推送 `ErrorEvent(reason="aborted")`

---

## 常见陷阱

| 陷阱 | 说明 |
|------|------|
| 忘记 `deepcopy(output)` | `push` 事件时传入的 `partial` 必须深拷贝，否则消费者看到的可能是后续修改后的状态 |
| 工具调用参数未解析 | 流式工具参数是增量片段，需要在 `finish_block` 时用 `parse_streaming_json` 解析 |
| 未处理 `CancelledError` | 消费者可能取消迭代，确保异常处理路径能正确结束 stream。`CancelledError` 必须原样抛出，不能转成 `StopAsyncIteration` |
| Unicode 代理项 | 文本内容通过 `sanitize_surrogates()` 清理后再发送给 API |
| 忘记更新 `ProviderStreamOptions` | 新增 `XxxOptions` 后必须在 `api_impls/__init__.py` 的 `ProviderStreamOptions` 联合类型中添加 |
