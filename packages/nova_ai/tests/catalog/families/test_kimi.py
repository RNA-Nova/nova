"""Kimi K3 族 ID 的归属边界矩阵。"""

import pytest
from nova_ai.catalog.families.kimi import is_kimi_k3_family_id


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("kimi-k3", True),
        ("kimi-k3.1", True),
        ("kimi-k3-256k", True),
        ("kimi-k3.1-pro", True),
        ("kimi-k3.1.2-pro", False),
        ("kimi-k30", False),
        ("kimi-k30-pro", False),
        ("kimi-k3x", False),
        ("KIMI-K3", False),
        ("", False),
    ],
)
def test_kimi_k3_family_id_boundaries(model_id, expected):
    assert is_kimi_k3_family_id(model_id) is expected
