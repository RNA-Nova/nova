"""
JSONRPCError 单元测试。

覆盖错误对象的 code、message、data 属性以及异常基类行为。
"""

import pytest

from nova_harness.modes.rpc.errors import JSONRPCError


class TestJSONRPCError:
    """JSONRPCError 行为测试。"""

    def test_stores_code_message_and_data(self):
        """应保存 code、message 与 data。"""
        err = JSONRPCError(code=-32600, message="Invalid Request", data={"detail": "x"})
        assert err.code == -32600
        assert err.message == "Invalid Request"
        assert err.data == {"detail": "x"}

    def test_default_data_is_none(self):
        """data 默认应为 None。"""
        err = JSONRPCError(code=-32700, message="Parse error")
        assert err.data is None

    def test_is_exception(self):
        """JSONRPCError 应继承自 Exception。"""
        err = JSONRPCError(code=-32603, message="Internal error")
        assert isinstance(err, Exception)
        assert str(err) == "Internal error"

    def test_can_be_raised(self):
        """应支持 raise/catch。"""
        with pytest.raises(JSONRPCError) as exc_info:
            raise JSONRPCError(-32601, "Method not found")
        assert exc_info.value.code == -32601
