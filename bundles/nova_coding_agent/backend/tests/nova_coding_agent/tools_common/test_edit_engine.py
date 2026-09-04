"""edit 编辑引擎语义测试。

覆盖：唯一性、原文匹配、重叠拒绝、原子性、fuzzy 匹配（智能引号/破折号/
行尾空白/NFKC）、无变化报错、CRLF/BOM 往返、diff 生成。
"""

import pytest
from nova_coding_agent.tools_common.edit_engine import (
    Edit,
    apply_edits_to_normalized_content,
    detect_line_ending,
    generate_diff_string,
    generate_unified_patch,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)


def test_exact_single_replacement():
    r = apply_edits_to_normalized_content(
        "hello world", [Edit("world", "nova")], "f.txt"
    )
    assert r.new_content == "hello nova"


def test_duplicate_occurrences_rejected():
    with pytest.raises(ValueError, match="2 occurrences.*unique"):
        apply_edits_to_normalized_content("aaa bbb aaa", [Edit("aaa", "x")], "f.txt")


def test_not_found_rejected():
    with pytest.raises(ValueError, match="Could not find"):
        apply_edits_to_normalized_content("hello", [Edit("nope", "x")], "f.txt")


def test_empty_old_text_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        apply_edits_to_normalized_content("hello", [Edit("", "x")], "f.txt")


def test_overlapping_edits_rejected():
    with pytest.raises(ValueError, match="overlap"):
        apply_edits_to_normalized_content(
            "abcdef", [Edit("abc", "x"), Edit("bcd", "y")], "f.txt"
        )


def test_no_change_rejected():
    with pytest.raises(ValueError, match="No changes made"):
        apply_edits_to_normalized_content("hello", [Edit("hello", "hello")], "f.txt")


def test_multiple_disjoint_edits_applied():
    r = apply_edits_to_normalized_content(
        "one two three four", [Edit("one", "1"), Edit("four", "4")], "f.txt"
    )
    assert r.new_content == "1 two three 4"


def test_fuzzy_match_smart_quotes_and_dash():
    """模型给 ASCII 引号/连字符，文件里是智能引号/长破折号 → fuzzy 命中。"""
    src = "say “hello” — ok\nnext line"
    r = apply_edits_to_normalized_content(
        src, [Edit('say "hello" - ok', "DONE")], "f.txt"
    )
    assert "DONE" in r.new_content
    # 未触及的行保留原始字节（fuzzy 叠回语义）
    assert "next line" in r.new_content


def test_fuzzy_match_trailing_whitespace():
    """模型给的 oldText 无行尾空白，文件行尾有多余空格 → fuzzy 命中。"""
    src = "foo   \nbar"
    r = apply_edits_to_normalized_content(src, [Edit("foo", "FOO")], "f.txt")
    assert r.new_content.startswith("FOO")


def test_fuzzy_no_false_positive_when_nothing_matches():
    with pytest.raises(ValueError, match="Could not find"):
        apply_edits_to_normalized_content(
            "hello", [Edit("completely different", "x")], "f.txt"
        )


def test_crlf_bom_roundtrip():
    bom, text = strip_bom("﻿a\r\nb\r\n")
    assert bom == "﻿"
    assert detect_line_ending(text) == "\r\n"
    r = apply_edits_to_normalized_content(normalize_to_lf(text), [Edit("b", "B")], "f")
    final = bom + restore_line_endings(r.new_content, "\r\n")
    assert final == "﻿a\r\nB\r\n"


def test_diff_string_line_numbers_and_first_change():
    diff, first = generate_diff_string("a\nb\nc", "a\nX\nc")
    assert first == 2
    assert "-2 b" in diff
    assert "+2 X" in diff


def test_diff_string_folds_long_context():
    old = "\n".join(f"line{i}" for i in range(1, 21))
    new = old.replace("line10", "CHANGED")
    diff, first = generate_diff_string(old, new)
    assert first == 10
    assert "..." in diff  # 中间折叠
    assert "-10 line10" in diff
    assert "+10 CHANGED" in diff


def test_unified_patch_format():
    patch = generate_unified_patch("f.txt", "a\nb", "a\nX")
    assert "--- f.txt" in patch
    assert "+++ f.txt" in patch
    assert "-b" in patch
    assert "+X" in patch


def test_fuzzy_preserves_unchanged_original_bytes():
    """fuzzy 命中时，未触及的行保留文件原始字节（不被归一化污染）。"""
    # 第二行含特殊空格（归一化会改写它，但它未被 edit 触及 → 必须原样保留）
    src = "target “quote”\nkeep　全角空格\n"
    r = apply_edits_to_normalized_content(
        src, [Edit('target "quote"', "DONE")], "f.txt"
    )
    assert "DONE" in r.new_content
    assert "keep　全角空格" in r.new_content
