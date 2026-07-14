"""测试 override 模式匹配。"""

import os

import pytest

from nova_harness.core.package.discovery import (
    apply_patterns,
    is_glob_pattern,
    is_override_pattern,
    matches_exact_pattern,
    matches_pattern,
)


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("!foo", True),
        ("+bar", True),
        ("-baz", True),
        ("foo", False),
    ],
)
def test_is_override_pattern(pattern: str, expected: bool) -> None:
    assert is_override_pattern(pattern) is expected


@pytest.mark.parametrize(
    "pattern,expected",
    [
        ("*.py", True),
        ("foo?", True),
        ("[abc].py", True),
        ("foo[0-9].txt", True),
        ("foo[]", False),
        ("foo", False),
    ],
)
def test_is_glob_pattern(pattern: str, expected: bool) -> None:
    assert is_glob_pattern(pattern) is expected


def test_matches_pattern(tmp_path) -> None:
    base = str(tmp_path)
    file_path = str(tmp_path / "extensions" / "foo.py")
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    assert matches_pattern(file_path, "*.py", base)
    assert matches_pattern(file_path, "extensions/*.py", base)
    assert not matches_pattern(file_path, "*.ts", base)


def test_matches_pattern_with_character_class(tmp_path) -> None:
    """字符类 glob（[...]）应被识别并匹配。"""
    base = str(tmp_path)
    py_file = str(tmp_path / "foo.py")
    ts_file = str(tmp_path / "foo.ts")

    assert matches_pattern(py_file, "*.[p]y", base)
    assert matches_pattern(ts_file, "*.[t]s", base)
    assert not matches_pattern(ts_file, "*.[p]y", base)


def test_normalize_path_for_compare_uses_base_dir(tmp_path) -> None:
    """_normalize_path_for_compare 应基于 base_dir 解析相对路径，而非进程 CWD。"""
    from nova_harness.core.package.discovery import _normalize_path_for_compare

    rel = "foo/bar.py"
    with_base = _normalize_path_for_compare(rel, base_dir=str(tmp_path))
    assert with_base == os.path.normpath(
        os.path.abspath(os.path.join(str(tmp_path), rel))
    )


def test_apply_patterns(tmp_path) -> None:
    base = str(tmp_path)
    all_paths = [
        str(tmp_path / "a.py"),
        str(tmp_path / "b.py"),
        str(tmp_path / "c.ts"),
    ]

    # include only .py
    result = apply_patterns(all_paths, ["*.py"], base)
    assert set(result) == {all_paths[0], all_paths[1]}

    # exclude .ts
    result = apply_patterns(all_paths, ["!*.ts"], base)
    assert set(result) == {all_paths[0], all_paths[1]}

    # exclude all .py then force-include one；force-include 只加回 a.py，不影响 c.ts
    result = apply_patterns(all_paths, ["!*.py", f"+{all_paths[0]}"], base)
    assert set(result) == {all_paths[0], all_paths[2]}

    # force-exclude wins
    result = apply_patterns(all_paths, ["*.py", f"-{all_paths[0]}"], base)
    assert set(result) == {all_paths[1]}
