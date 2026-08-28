# 维护手册：添加新的模型

本文档说明如何在 `nova_ai` 中添加新的 LLM 模型定义。

---

## 模型数据结构

```python
@dataclass
class Model(NovaBaseModel):
    id: str                          # 模型唯一标识
    name: str                        # 展示名称
    api: str                         # 使用的 API 协议（KnownApi 枚举值）
    provider: str                    # 提供商名称（KnownProvider 枚举值）
    base_url: str                    # API 基础 URL
    max_tokens: int                  # 最大输出 token 数
    context_window: int              # 上下文窗口大小
    input_types: List[str]           # 支持的输入类型（["text", "image"]）
    cost: ModelCost                  # 成本定义
    reasoning: bool = False          # 是否支持推理/思考
    compat: Optional[OpenAICompletionsCompat] = None  # 兼容性覆盖
    thinking_level_map: Optional[Dict[str, str]] = None  # 思考级别映射
    headers: Optional[Dict[str, str]] = None  # 额外请求头
```

---

## 添加步骤

### 第 1 步：在厂商模型数据文件中定义

当前 `models/` 目录下只有 `volcengine.py`。如果新增厂商，新建文件（如 `models/new_provider.py`）：

```python
# models/new_provider.py
from typing import Dict
from ..types.model import Model, ModelCost
from ..types.enums import KnownApi, KnownProvider

NEW_PROVIDER_MODELS: Dict[str, Model] = {
    "model-id": Model(
        id="model-id",
        name="Model Name",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.NEW_PROVIDER,
        base_url="https://api.example.com/v1/",
        max_tokens=4096,
        context_window=32768,
        input_types=["text"],
        reasoning=False,
        cost=ModelCost(
            input=0.8,
            output=2.0,
            cache_read=0.0,
            cache_write=0.0,
        ),
    ),
}
```

### 第 2 步：更新 `models/__init__.py`

```python
from .new_provider import NEW_PROVIDER_MODELS

__all__ = [
    # ...已有导出...
    "NEW_PROVIDER_MODELS",
]
```

### 第 3 步：注册到 builtins

在 `registry/builtins.py` 中使用 `KnownProvider` 枚举注册：

```python
def register_builtin_models() -> None:
    from ..models.new_provider import NEW_PROVIDER_MODELS
    
    register_models_from_dict(KnownProvider.NEW_PROVIDER, NEW_PROVIDER_MODELS)
```

如果 `KnownProvider` 枚举中没有对应值，先在 `types/enums.py` 中添加。

### 第 4 步：添加环境变量支持（如需）

如果新提供商需要 API key，在 `utils/env.py` 中添加映射：

```python
def get_env_api_key(provider: str) -> Optional[str]:
    env_map = {
        # ...现有映射...
        "new_provider": "NEW_PROVIDER_API_KEY",
    }
    env_var = env_map.get(provider)
    return os.environ.get(env_var) if env_var else None
```

---

## 成本字段单位

`ModelCost` 的单位是 **$/M tokens**（每百万 token 的美元价格）：

```python
cost=ModelCost(
    input=0.5,        # $0.5 / 1M input tokens
    output=1.5,       # $1.5 / 1M output tokens
    cache_read=0.1,   # $0.1 / 1M cache read tokens
    cache_write=1.0,  # $1.0 / 1M cache write tokens
)
```

实际成本计算在 `utils/model_utils.py` 的 `calculate_cost()` 中：

```python
def calculate_cost(model: Model, usage: Usage) -> Cost:
    usage.cost.input = (model.cost.input / 1000000) * usage.input
    usage.cost.output = (model.cost.output / 1000000) * usage.output
    # ...
```

---

## 思考级别映射

如果模型支持 reasoning，可以定义 `thinking_level_map` 来映射思考级别：

```python
thinking_level_map={
    "off": "disabled",
    "minimal": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    # "xhigh": None,  # 显式标记为不支持
}
```

规则：

- 如果 `reasoning=False`，`get_supported_thinking_levels()` 只返回 `[ModelThinkingLevel.OFF]`
- 如果 `thinking_level_map` 中某级别映射为 `None`，该级别不受支持
- `xhigh` 默认不受支持，除非 `thinking_level_map` 中显式定义

---

## 兼容性配置覆盖

如果模型需要覆盖自动检测的兼容性设置，可以在 `compat` 字段中显式指定：

```python
from ..types.compat import OpenAICompletionsCompat

Model(
    # ...其他字段...
    compat=OpenAICompletionsCompat(
        supports_store=False,
        max_tokens_field="max_tokens",
    ),
)
```

`compat` 中的非 None 字段会覆盖 `detect_compat()` 的自动检测结果。

兼容性配置类型定义在 `types/compat.py` 中。添加新的 compat 字段时，同时更新 `types/compat.py` 中的数据类定义。

---

## 测试要求

新增模型后，需要验证：

1. **注册测试** —— 模型能通过 `get_model()` / `get_model_by_id()` 正确查询
2. **成本计算测试** —— `calculate_cost()` 对新增模型计算正确
3. **思考级别测试**（如支持 reasoning）— `get_supported_thinking_levels()` 返回预期列表
