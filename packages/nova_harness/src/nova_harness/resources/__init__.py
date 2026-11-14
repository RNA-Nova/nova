"""资源发现、加载与公共数据类型。

导入纪律：公共类型聚合在 ``nova_harness.resources.types``；包级入口不做
急切 re-export，避免 config、extensions 与 project_trust 之间形成循环导入。
"""
