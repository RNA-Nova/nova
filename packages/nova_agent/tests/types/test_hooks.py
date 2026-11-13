"""工具钩子结果值对象的不可变契约。"""

import dataclasses

import pytest

from nova_agent import BeforeToolCallResult


def test_hook_result_is_frozen():
    result = BeforeToolCallResult(block=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(result, "block", False)
