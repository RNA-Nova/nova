"""权限套餐继承合并的输入不可变性测试。"""

import tomllib

from nova_exec_policy import PermissionsToml


def test_permission_merge_does_not_mutate_parent_or_child_inputs():
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[base.network.domains]
"BASE.EXAMPLE.COM." = "deny"

[child]
extends = "base"

[child.network.domains]
"CHILD.EXAMPLE.COM." = "allow"
"""))
    before = permissions.model_dump()
    permissions.resolve_profile("child", lambda _name: None)
    assert permissions.model_dump() == before
