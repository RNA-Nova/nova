"""路径解析 macOS 文件名变体重试测试。"""

import os
import unicodedata

from nova_coding_agent.tools_common import path_utils
from nova_coding_agent.tools_common.path_utils import resolve_path


def test_resolve_existing_path_unchanged(tmp_path):
    """存在的路径原样返回，不走变体重试。"""
    p = tmp_path / "plain.txt"
    p.write_text("x")
    assert resolve_path(str(p)) == os.path.normpath(str(p))


def test_resolve_relative_against_cwd(tmp_path):
    """相对路径以 cwd 为基准解析。"""
    p = tmp_path / "rel.txt"
    p.write_text("x")
    assert resolve_path("rel.txt", str(tmp_path)) == os.path.normpath(str(p))


def test_empty_path_returns_empty():
    assert resolve_path("") == ""


# ---------------------------------------------------------------------------
# 初始输入归一（Unicode 空格 + @ 前缀）
# ---------------------------------------------------------------------------


def test_at_prefix_stripped(tmp_path):
    """@ 前缀剥离（CLI @file 形态）。"""
    p = tmp_path / "at.txt"
    p.write_text("x")
    assert resolve_path("@" + str(p)) == os.path.normpath(str(p))


def test_unicode_spaces_normalized(tmp_path):
    """输入带窄不间断空格（U+202F）：初始归一为普通空格后命中。"""
    p = tmp_path / "a b.txt"
    p.write_text("x")
    queried = str(p)[: -len("a b.txt")] + "a\u202fb.txt"
    assert resolve_path(queried) == os.path.normpath(str(p))


# ---------------------------------------------------------------------------
# 真实文件系统变体命中
# ---------------------------------------------------------------------------


def test_am_pm_narrow_no_break_space_variant(tmp_path):
    """macOS 截图文件名：AM/PM 前是窄不间断空格，普通空格输入重试命中。"""
    p = tmp_path / "Screen Shot 2024-01-01 at 3.00\u202fPM.png"
    p.write_bytes(b"x")
    queried = str(p).replace("\u202fPM.", " PM.")
    assert queried != str(p)
    resolved = resolve_path(queried)
    assert resolved == os.path.normpath(str(p))
    assert os.path.exists(resolved)


def test_curly_quote_variant(tmp_path):
    """弯引号（U+2019）文件名：直引号输入重试命中。"""
    p = tmp_path / "what\u2019s up.png"
    p.write_bytes(b"x")
    queried = str(p).replace("\u2019", "'")
    assert queried != str(p)
    resolved = resolve_path(queried)
    assert resolved == os.path.normpath(str(p))
    assert os.path.exists(resolved)


def test_nfd_variant_real_fs(tmp_path):
    """NFD 存储的文件名：NFC 输入最终可读。

    macOS APFS 比较时规范化不敏感，第一级 exists 即可能命中（返回 NFC
    形式）；其他平台经 NFD 变体重试命中（返回 NFD 形式）——两者都合法。
    """
    p = tmp_path / unicodedata.normalize("NFD", "café.txt")
    p.write_text("x")
    queried = str(tmp_path / unicodedata.normalize("NFC", "café.txt"))
    resolved = resolve_path(queried)
    assert os.path.exists(resolved)
    assert os.path.basename(resolved) in (
        unicodedata.normalize("NFD", "café.txt"),
        unicodedata.normalize("NFC", "café.txt"),
    )


def test_nonexistent_returns_resolved(tmp_path):
    """所有变体都不存在：返回原解析结果。"""
    missing = str(tmp_path / "café 3.00 PM what's.png")
    assert resolve_path(missing) == os.path.normpath(missing)


# ---------------------------------------------------------------------------
# 重试链顺序（monkeypatch exists，排除文件系统差异干扰）
# ---------------------------------------------------------------------------


def test_nfd_variant_retry(monkeypatch, tmp_path):
    """NFC 输入经重试链命中 NFD 存储（强制走重试）。"""
    nfd = str(tmp_path / unicodedata.normalize("NFD", "café.txt"))
    monkeypatch.setattr(path_utils.os.path, "exists", lambda path: path == nfd)
    nfc = str(tmp_path / unicodedata.normalize("NFC", "café.txt"))
    assert resolve_path(nfc) == nfd


def test_am_pm_variant_takes_precedence_over_nfd(monkeypatch, tmp_path):
    """AM/PM 变体优先于 NFD 变体。"""
    base = str(tmp_path / "café 3.00 PM.png")
    am_pm = base.replace(" PM.", "\u202fPM.")
    nfd = unicodedata.normalize("NFD", base)
    existing = {am_pm, nfd}

    monkeypatch.setattr(path_utils.os.path, "exists", lambda path: path in existing)
    assert resolve_path(base) == am_pm


def test_nfd_curly_combined_variant(monkeypatch, tmp_path):
    """NFD+弯引号组合变体：NFC+直引号输入命中。"""
    nfc_curly = str(tmp_path / "café d\u2019ete.png")
    target = unicodedata.normalize("NFD", nfc_curly)
    monkeypatch.setattr(path_utils.os.path, "exists", lambda path: path == target)
    queried = str(tmp_path / "café d'ete.png")  # NFC + 直引号
    assert resolve_path(queried) == target
