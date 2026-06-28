# ADR-004: 导入规则

## 状态

已接受

## 背景

`nova_ai` 包内部模块众多，导入关系容易混乱。需要一套明确的导入规则来保持代码整洁。

## 决策

建立两级导入规则：同层直接导入，跨层走 `__init__.py`。

## 规则

### 规则 1：同层目录内直接互相导入

同一目录（如 `types/` 内部、`registry/` 内部）的文件之间，直接导入子模块：

```python
# ✅ 正确 —— types/ 内部文件之间直接导入
from .model import Model
from .enums import Api
from .messages import AssistantMessage

# ✅ 正确 —— registry/ 内部文件之间直接导入
from .api_registry import clear_api_adapters, register_api_adapter
from .model_registry import clear_model_registry
```

### 规则 2：跨层导入走 `__init__.py` 统一导出

不同目录之间（如 `streaming/` 导入 `types/`、`registry/` 导入 `api_impls/`），使用上层 `__init__.py` 的统一导出：

```python
# ✅ 正确 —— streaming/ 导入 types/
from ..types import Api, Model, AssistantMessage

# ✅ 正确 —— registry/ 导入 api_impls/
from ..api_impls import stream_openai_completions

# ✅ 正确 —— 根包入口统一导出
from .types import Model
from .streaming import stream
```

**例外**：如果 `__init__.py` 没有导出某个符号，且该符号是内部使用的，可以直接从子模块导入。但新增公共类型时，应优先更新 `__init__.py` 的导出列表。

### 规则 3：同包内用相对导入

```python
# ✅ 正确 —— 同包内用相对导入
from .event_stream import AssistantMessageEventStream
from .invoke import stream

# ❌ 错误 —— 同包内不要用绝对导入
from nova_ai.streaming.event_stream import AssistantMessageEventStream
```

跨包（如 `streaming/` 导入 `types/`）用相对导入的上级语法：

```python
from ..types import Model
from ..registry.api_registry import get_api_adapter
```

### 规则 4：禁止绕路导入

不允许通过中间模块间接导入最终来源：

```python
# ❌ 错误 —— 以前 providers/ 中间层导出了 Model
from ..providers import Model

# ✅ 正确 —— 直接从类型定义处导入
from ..types.model import Model
```

## 规则图示

```
外部调用者
    ↓
nova_ai/__init__.py  ←—— 包级公共 API（从各子包 __init__.py 重新导出）
    ↓
streaming/__init__.py  ←—— 流式模块统一导出
registry/__init__.py   ←—— 注册表统一导出
api_impls/__init__.py  ←—— 协议实现统一导出
    ↓
各子模块内部       ←—— 同层内自由直接导入子模块
```

## 当前导出现状

| 模块 | `__init__.py` 导出内容 |
|------|----------------------|
| `types/__init__.py` | 所有基础类型（Model、Message、Event、Compat 等） |
| `streaming/__init__.py` | EventStream、stream/complete/stream_simple/complete_simple |
| `registry/__init__.py` | ApiRegistry、ModelRegistry、便捷函数、builtins |
| `api_impls/__init__.py` | stream_openai_completions、OpenAICompletionsOptions、ProviderStreamOptions |
| `models/__init__.py` | Model、ModelCost、VOLCENGINE_MODELS、get/list 函数 |
| `utils/__init__.py` | 环境变量、Copilot、JSON 解析、消息转换等工具 |

## 后果

- **正面**：导入路径清晰、减少循环导入风险、公共 API 边界明确
- **负面**：需要维护多个 `__init__.py` 的导出列表。新增公共类型/工具时需要同步更新对应层的 `__init__.py`，以及根包的 `__init__.py`
