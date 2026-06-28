# ADR-001: ApiAdapter 为什么用 Protocol 而不是 ABC

## 状态

已接受

## 背景

`ApiAdapter` 定义了 API 协议实现必须满足的契约：

- `api: str` —— 标识所属的 API 协议类型
- `stream()` —— 流式调用
- `stream_simple()` —— 简化流式调用

在实现这个契约时，有两种选择：

1. **ABC 抽象基类** —— 定义抽象方法，子类继承并实现
2. **Protocol** —— 定义类型契约，任何满足该结构的对象都视为合法实现

## 决策

使用 `typing.Protocol`，不使用 ABC。

## 理由

### 1. 没有多态需求

`ApiAdapter` 从不会被"统一处理"。`ApiRegistry` 存的是一个个独立的 adapter 对象，调用方只关心 `adapter.stream(model, context, options)` 能执行，不关心它的继承关系。

如果用 ABC：

```python
class ApiAdapter(ABC):
    @abstractmethod
    def stream(self, ...): ...

class OpenAIAdapter(ApiAdapter):
    def stream(self, ...): ...
```

带来的好处是零 —— 没有地方需要 `isinstance(adapter, ApiAdapter)` 检查，也没有多个 adapter 实现需要共享基类方法。

### 2. Protocol 更轻量

Protocol 是纯类型契约，运行时零开销。适配器实现者只需要提供一个包含 `api` 属性以及 `stream`、`stream_simple` 方法的对象即可，不需要继承任何类。

```python
# 合法实现，不需要继承
class OpenAICompletionsAdapter:
    api = "openai-completions"

    def stream(self, model, context, options=None): ...
    def stream_simple(self, model, context, options=None): ...
```

### 3. 避免循环导入

`ApiAdapter` 定义在 `types/api_adapter.py`，而 `api_impls/openai_completions.py` 需要引用它。如果用 ABC，类型检查时会形成包内循环依赖。Protocol 可以在 `TYPE_CHECKING` 块中引用，避免运行时循环导入。

## 后果

- **正面**：实现者无继承负担，运行时零开销，无循环导入风险
- **负面**：没有运行时强制检查，如果 adapter 漏实现了某个方法，要到实际调用时才报错。但这个问题在注册时通过调用测试即可覆盖

## 相关讨论

- 2026-06-10：确认 `registry/` 下的注册表也不应改为 ABC（两个注册表的接口签名完全不同，抽象不出通用 ABC）
