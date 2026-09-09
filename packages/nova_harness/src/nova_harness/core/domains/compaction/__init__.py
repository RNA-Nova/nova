"""上下文压缩与分支摘要工具。

导入纪律：走完整子模块路径（compaction / branch_summarization / utils / types），
不在包级 ``__init__`` 做 re-export——events(auto) → 压缩类型 → sessions →
extensions 的急切级联会形成循环 import（旧约定在此处已被重构掉）。
"""
