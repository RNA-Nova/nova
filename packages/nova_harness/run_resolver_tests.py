"""临时测试入口：绕过因重构删除导致的顶层 import 失败。"""

import os
import sys
from types import ModuleType

# 预置空包，避免执行 nova_harness/__init__.py 和 core/__init__.py
sys.modules["nova_harness"] = ModuleType("nova_harness")
sys.modules["nova_harness"].__path__ = ["src/nova_harness"]
sys.modules["nova_harness.core"] = ModuleType("nova_harness.core")
sys.modules["nova_harness.core"].__path__ = ["src/nova_harness/core"]

import pytest

conftest = "tests/conftest.py"
conftest_bak = "tests/conftest.py.bak"
renamed = False
if os.path.exists(conftest):
    os.rename(conftest, conftest_bak)
    renamed = True

try:
    sys.exit(pytest.main(["tests/core/package/resolver", "-q"]))
finally:
    if renamed:
        os.rename(conftest_bak, conftest)
