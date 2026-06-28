# 代码约定

本文档汇总 `nova_ai` 包内的代码风格、架构约定和最佳实践。

---

## 命名约定

| 类型 | 风格 | 示例 |
|------|------|------|
| 类名 | PascalCase | `EventStream`, `AssistantMessage` |
| 函数/变量 | snake_case | `stream_openai_completions`, `api_registry` |
| 常量 | UPPER_CASE | `TIME_OUT = 60`, `_END_SENTINEL = object()` |
| 私有属性 | 下划线前缀 | `_queue`, `_done`, `_is_complete` |
| 类型变量 | 单大写字母 | `T`, `R` |
| 枚举成员 | UPPER_CASE | `KnownApi.OPENAI_COMPLETIONS` |

---

## 导入规则

详见 [ADR-004: 导入规则](../adr/004-import-rules.md)。

### 快速参考

```python
# ✅ 正确 —— 同层目录内直接导入子模块
from .model import Model
from .enums import Api

# ✅ 正确 —— 跨层导入走 __init__.py 统一导出
from ..types import Model, AssistantMessage
from ..api_impls import stream_openai_completions

# ✅ 正确 —— 同包内用相对导入
from .event_stream import AssistantMessageEventStream

# ❌ 错误 —— 同包内不要用绝对导入
from nova_ai.streaming.event_stream import AssistantMessageEventStream
```

---

## 类型系统

### 使用 Pydantic v2

```python
from typing import List
from ..types.base_model import NovaBaseModel

class MyType(NovaBaseModel):
    name: str
    items: List[str] = []
```

`NovaBaseModel` 继承自 `pydantic.BaseModel`，配置了 `use_enum_values=True`（Enum 序列化为字符串）和 `validate_assignment=True`（属性赋值时自动验证）。

### 类型注解

- 所有公共函数参数和返回值必须加类型注解
- 使用 `Optional[X]` 而不是 `X | None`（兼容 Python 3.9）
- 使用 `List[X]`、`Dict[K, V]` 而不是 `list[X]`、`dict[K, V]`（兼容 Python 3.9）

---

## 注释与文档字符串

- 以**中文**为主
- 模块级 docstring 说明模块职责
- 公共函数使用 Google 风格 docstring：

```python
def calculate_cost(model: Model, usage: Usage) -> Cost:
    """
    根据模型和用量计算成本

    Args:
        model: 模型对象
        usage: 使用统计

    Returns:
        成本明细
    """
```

---

## 异常处理

### asyncio.CancelledError 必须原样抛出

```python
# ✅ 正确
try:
    item = await waiter
except asyncio.CancelledError:
    # 清理资源
    self._waiting.remove(waiter)
    raise  # 原样抛出

# ❌ 错误 —— 吞掉取消语义
try:
    item = await waiter
except asyncio.CancelledError:
    raise StopAsyncIteration  # 破坏了外层的取消信号
```

### 流式处理中的异常

错误事件通过 `ErrorEvent` 推送，而不是直接抛异常中断流：

```python
except Exception as e:
    output.stop_reason = StopReason.ERROR
    output.error_message = str(e)
    stream.push(ErrorEvent(reason=StopReason.ERROR, error=deepcopy(output)))
    stream.end()
```

---

## 深拷贝约定

`push` 事件时传入的 `partial` 参数必须深拷贝，避免消费者看到后续修改：

```python
# ✅ 正确
stream.push(TextDeltaEvent(
    content_index=current_block_index,
    delta=delta.content,
    partial=deepcopy(output)  # 必须深拷贝
))

# ❌ 错误
stream.push(TextDeltaEvent(
    content_index=current_block_index,
    delta=delta.content,
    partial=output  # 消费者会看到后续修改
))
```

---

## 模块分层

```
外部调用者
    ↓
nova_ai/__init__.py  ←—— 包级公共 API（从各子包 __init__.py 重新导出）
    ↓
streaming/invoke.py  ←—— 调用入口层（stream/complete 等）
    ↓
registry/            ←—— API adapter 查找、模型查询
    ↓
api_impls/           ←—— 协议实现（OpenAI Completions 等）
    ↓
types/               ←—— 基础类型（最底层，不依赖任何业务模块）
utils/               ←—— 工具函数（可被任何层使用）
models/              ←—— 厂商模型静态数据（依赖 types/）
```

**依赖方向**：只能从上到下，不能反向。`types/` 不能依赖 `utils/`，`registry/` 不能依赖 `api_impls/`。

**已合并的目录**：
- `core/` → `types/`（基础类型全部合并到 types/）
- `auth/` → 已移除（鉴权辅助功能暂未实现）
- `compat/` → `types/compat.py`（兼容性类型定义合并到 types/）

---

## 测试

- 使用 `pytest`
- 测试文件名以 `test_` 开头
- 每个测试函数名以 `test_` 开头
- 导入测试放在 `tests/test_imports.py`，确保公共 API 可正常导入
