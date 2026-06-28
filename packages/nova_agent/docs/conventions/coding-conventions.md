# 代码约定

本文档汇总 `nova_agent` 包内的代码风格、架构约定和最佳实践。

---

## 命名约定

| 类型 | 风格 | 示例 |
|------|------|------|
| 类名 | PascalCase | `Agent`, `AgentLoopConfig` |
| 函数/变量 | snake_case | `run_agent_loop`, `before_tool_call` |
| 常量 | UPPER_CASE | `APP_NAME = "nova_agent"` |
| 私有属性 | 下划线前缀 | `_state`, `_listeners` |
| 类型变量 | 单大写字母 | `T`, `R` |

---

## 导入规则

- 同层目录内直接导入子模块
- 跨层导入走 `__init__.py` 统一导出
- 同包内用相对导入

```python
# ✅ 正确
from .tools import execute_tool_calls
from ..types import AgentContext
from nova_ai import AssistantMessage

# ❌ 错误 —— 同包内不要用绝对导入
from nova_agent.agent_loop.tools import execute_tool_calls
```

---

## 类型系统

- 所有公共函数参数和返回值必须加类型注解
- 使用 `Optional[X]` 而不是 `X | None`（兼容 Python 3.9）
- 使用 `List[X]`、`Dict[K, V]` 而不是 `list[X]`、`dict[K, V]`（兼容 Python 3.9）

---

## 注释与文档字符串

- 以**中文**为主
- 模块级 docstring 说明模块职责
- 复杂函数使用 Google 风格 docstring

---

## 测试约定

- 单元测试放在 `tests/`
- mock stream 测试需考虑 abort / block 后的后续 assistant 响应
- 集成测试标记 `@pytest.mark.integration`

---

## 序列化

- 优先使用标准 `dataclass` + `mashumaro` 做 JSON 序列化
- 不使用 Pydantic 定义 agent 核心类型（但允许依赖 nova_ai 的 Pydantic 类型）
