"""Tests for dependency utilities."""

import tempfile

from nova_harness.core.package.deps import extract_package_name


def test_extract_package_name_simple():
    assert extract_package_name("requests>=2.0") == "requests"


def test_extract_package_name_with_extras():
    assert extract_package_name("requests[socks]>=2.28") == "requests"


def test_extract_package_name_url():
    assert extract_package_name("pkg @ git+https://example.com/pkg.git") == "pkg"


def test_extract_package_name_editable_path():
    with tempfile.TemporaryDirectory() as d:
        import os

        # 没有 pyproject.toml 时 fallback 到目录名
        assert (
            extract_package_name(f"-e {d}")
            == os.path.basename(d).replace("_", "-").lower()
        )


def test_extract_package_name_empty():
    assert extract_package_name("") is None
