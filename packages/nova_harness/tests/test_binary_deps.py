"""Tests for optional binary dependency detection."""

from nova_harness.core.package.binary_deps import (
    detect_missing_binaries,
    format_binary_hints,
)


def test_detect_missing_binaries():
    # "python" 必定存在，"this_binary_definitely_missing_xxx" 必定不存在
    binary_map = {
        "python": "python3",
        "this_binary_definitely_missing_xxx": "xxx-package",
    }
    missing = detect_missing_binaries(binary_map)
    assert "python" not in missing
    assert "this_binary_definitely_missing_xxx" in missing


def test_format_binary_hints():
    hints = format_binary_hints({"rg": "ripgrep"})
    assert "rg" in hints
    assert "ripgrep" in hints


def test_format_binary_hints_empty():
    assert format_binary_hints({}) == ""
