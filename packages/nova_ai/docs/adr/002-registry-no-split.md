# ADR-002: 为什么全局单例和 Registry 类定义放在同一个文件

## 状态

已接受

## 背景

`registry/` 下有两个注册表：

- `ApiRegistry` —— 管理 API adapter 注册
- `ModelRegistry` —— 管理模型注册

每个注册表都包含：

1. `Registry` 类定义（数据结构和操作方法）
2. 全局单例实例（`_api_registry = ApiRegistry()` / `_model_registry = ModelRegistry()`）
3. 便捷函数（`register_api_adapter()`、`get_model()` 等）

有人可能会提议把单例和便捷函数拆到单独的 `global_registry.py` 中，让类定义文件更"纯粹"。

## 决策

维持现状：类定义 + 全局单例 + 便捷函数放在同一个文件中。

## 理由

### 1. 规模可控

- `api_registry.py`：109 行
- `model_registry.py`：202 行

拆成 4 个文件（每个类的定义文件 + 全局实例文件）后，每个文件 50~100 行，过度碎片化。

### 2. 紧耦合

便捷函数直接引用全局实例：

```python
def get_api_adapter(api: Union[Api, str]) -> Optional[ApiAdapter]:
    return _api_registry.get(api)
```

拆开只是多一层 import，没有解耦任何实质依赖。

### 3. Python 惯例

标准库 `logging` 模块也是这种模式：

```python
# logging/__init__.py 中
class Logger: ...        # 类定义
root = RootLogger(...)   # 全局单例
def getLogger(name): ... # 便捷函数
```

### 4. 测试无影响

测试可以直接 `ApiRegistry()` 创建新实例，不需要碰全局单例。全局实例的存在不影响类的可测试性。

### 5. 何时应该拆

- 文件增长到 300+ 行
- 需要多个全局实例（如项目级 vs 全局级注册表）
- 需要序列化/持久化逻辑

当前均不满足。

## 后果

- **正面**：文件数量少，import 路径短，维护成本低
- **负面**：如果未来初始化变重（如从配置文件加载），导入时创建全局对象会有性能问题。届时可改为懒加载（`Optional[Registry]` + 工厂函数）
