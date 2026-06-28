# 维护手册：新增数据类

本文档说明在 `nova_ai` 中新增数据类的规范。

---

## 基类

所有数据类必须继承 `NovaBaseModel`：

```python
from typing import List, Optional
from ..types.base_model import NovaBaseModel

class MyNewType(NovaBaseModel):
    name: str
    items: List[str] = []
    metadata: Optional[dict] = None
```

`NovaBaseModel` 继承自 `pydantic.BaseModel`，配置了 `use_enum_values=True`（Enum 序列化为字符串）和 `validate_assignment=True`（属性赋值时自动验证）。

---

## 字段定义规范

### 1. 使用 Pydantic 模型语法

```python
from typing import Optional, List, Literal, Dict, Any
from ..types.base_model import NovaBaseModel

class MyEvent(NovaBaseModel):
    type: Literal["my_event"] = "my_event"
    content: str
    index: int = 0
    metadata: Optional[Dict[str, Any]] = None
    tags: List[str] = []
```

### 2. 可变性字段用默认值

Pydantic v2 自动处理可变默认值，不需要 `default_factory`：

```python
# ✅ 正确 —— Pydantic 会自动为每个实例创建新的列表
items: List[str] = []
config: Dict[str, Any] = {}
```

### 3. Literal 类型用于区分事件/枚举

事件类型字段使用 `Literal` 确保类型安全：

```python
from typing import Literal

class TextStartEvent(NovaBaseModel):
    type: Literal["text_start"] = "text_start"

class TextDeltaEvent(NovaBaseModel):
    type: Literal["text_delta"] = "text_delta"
```

Pydantic 会自动验证 `Literal` 字段的值是否在允许范围内。

---

## 文件位置

### 类型定义放 `types/`

```
types/
├── base_model.py      # NovaBaseModel 基类（pydantic.BaseModel）
├── content.py         # 内容块（TextContent, ThinkingContent, ToolCall, ImageContent）
├── messages.py        # 消息类型（Message, AssistantMessage, ToolResultMessage, Context, Tool）
├── events.py          # 流事件（StartEvent, TextDeltaEvent, DoneEvent, ErrorEvent 等）
├── model.py           # 模型定义（Model, ModelCost, Usage, Cost）
├── stream_options.py  # 流选项（StreamOptions, SimpleStreamOptions）
├── compat.py          # 兼容性配置
├── enums.py           # 枚举（Api, KnownProvider, StopReason, ThinkingLevel 等）
└── api_adapter.py     # ApiAdapter Protocol
```

**规则**：如果新类型属于已有类别，追加到对应文件。如果创建全新类别（如 "缓存配置"），可以新建文件。

### 不要在 `types/` 中引入业务依赖

`types/` 目录必须是包内最底层，**不能依赖任何业务模块**（如 `utils/`、`registry/`、`streaming/`、`api_impls/`）。

```python
# ✅ 正确 —— types/ 内部互相导入
from .content import TextContent
from .enums import StopReason

# ❌ 错误 —— types/ 不能依赖上层模块
from ..utils.env import get_env_api_key
from ..registry import get_model
```

---

## 导出规则

新增类型后，需要在 `types/__init__.py` 中导出：

```python
from .my_new_module import MyNewType

__all__ = [
    # ...已有导出...
    "MyNewType",
]
```

同时在包根 `__init__.py` 中导出（如果是公共 API）：

```python
from .types import MyNewType
```

---

## JSON 序列化注意事项

`NovaBaseModel.model_dump()` 默认使用 `mode='json'`，确保输出纯 Python 原生类型（如 Enum → str）：

```python
my_event = MyEvent(type="my_event", content="hello")
data = my_event.model_dump()  # Dict[str, Any]
json_str = my_event.model_dump_json()  # str
```

如需自定义字段别名，使用 Pydantic 的 `Field(alias=...)`：

```python
from pydantic import Field

class MyType(NovaBaseModel):
    my_field: str = Field(alias="myField")
```

但项目中**尽量避免使用别名**，保持 Python 字段名和 JSON key 一致（都用 `snake_case`）。

---

## 常见错误

| 错误 | 说明 |
|------|------|
| 忘记继承 `NovaBaseModel` | 所有数据类必须显式继承 `NovaBaseModel` |
| 可变默认参数 | Pydantic v2 自动处理，直接用 `[]` 或 `{}` 即可 |
| 循环导入 | `types/` 内部文件互相导入时，用 `from .xxx import` 相对导入，避免绝对路径循环 |
| 未在 `__init__.py` 导出 | 新增类型后忘记更新 `types/__init__.py` 和根 `__init__.py` |
