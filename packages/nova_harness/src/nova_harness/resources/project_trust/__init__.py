"""Project Trust 模块。

负责项目级资源（``.nova/`` 配置、扩展、skills 等）的信任门控决策与持久化。

导入纪律：走完整子模块路径（callback / project_trust / trust_store / types），
不在包级 ``__init__`` 做 re-export——本域与 config 域存在交叉引用，
急切 re-export 会形成循环 import（旧约定在此处已被重构掉）。
"""
