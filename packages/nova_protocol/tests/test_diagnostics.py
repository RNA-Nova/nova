"""NovaDiagnostic（diagnostics.py）测试——不可变值对象形状。"""

from dataclasses import FrozenInstanceError

import pytest
from nova_protocol import NovaDiagnostic, WarningKind


class TestNovaDiagnostic:
    def test_kw_only_and_frozen(self):
        diagnostic = NovaDiagnostic(
            severity="warning", kind=WarningKind.CONFIG, message="settings v1 已迁移"
        )
        assert diagnostic.severity == "warning"
        with pytest.raises(FrozenInstanceError):
            diagnostic.message = "改"  # type: ignore[misc]
        with pytest.raises(TypeError):
            NovaDiagnostic("warning", WarningKind.CONFIG, "位置参数不可")  # type: ignore[misc]
