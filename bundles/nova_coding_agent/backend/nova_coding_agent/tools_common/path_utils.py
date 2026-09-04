"""路径解析辅助函数。"""

import os
import re
import unicodedata
from typing import List, Optional

# Unicode 空格变体（归一为普通空格）
_UNICODE_SPACES = re.compile("[\u00a0\u2000-\u200a\u202f\u205f\u3000]")

# 窄不间断空格 U+202F（macOS 截图文件名 "3.00 PM" 中 AM/PM 前的分隔符）
_NARROW_NO_BREAK_SPACE = "\u202f"

# macOS 系统生成文件名（如法语截图 "Capture d'écran"）使用的弯引号 U+2019；
# 用户通常输入直引号 U+0027
_CURLY_QUOTE = "\u2019"

# AM/PM 前的普通空格
_AM_PM_SPACE = re.compile(r" (AM|PM)\.", re.IGNORECASE)


def normalize_input(path: str) -> str:
    """输入归一。"""
    path = _UNICODE_SPACES.sub(" ", path)
    if path.startswith("@"):
        path = path[1:]
    return path


def _macos_path_variants(resolved: str) -> List[str]:
    """macOS 文件名变体重试链（按序）。

    顺序：AM/PM 窄不间断空格 → NFD 分解 → 弯引号 → NFD+弯引号组合；
    与 resolved 相同的变体跳过。
    """
    variants: List[str] = []
    # macOS 截图文件名 AM/PM 前是窄不间断空格，用户输入的是普通空格
    am_pm = _AM_PM_SPACE.sub(_NARROW_NO_BREAK_SPACE + r"\g<1>.", resolved)
    if am_pm != resolved:
        variants.append(am_pm)
    # macOS 以 NFD（分解形式）存储文件名，用户输入多为 NFC
    nfd = unicodedata.normalize("NFD", resolved)
    if nfd != resolved:
        variants.append(nfd)
    # 直引号 → 弯引号
    curly = resolved.replace("'", _CURLY_QUOTE)
    if curly != resolved:
        variants.append(curly)
    # NFD + 弯引号组合（法语截图 "Capture d'écran" 同时踩两条）
    nfd_curly = nfd.replace("'", _CURLY_QUOTE)
    if nfd_curly != resolved and nfd_curly not in variants:
        variants.append(nfd_curly)
    return variants


def resolve_path(path: str, cwd: Optional[str] = None) -> str:
    """把相对路径解析为绝对路径（默认以当前工作目录为基准），支持 ``~`` 展开。

    输入先做 Unicode 空格归一与 ``@`` 前缀剥离；
    解析结果不存在时按 macOS 文件名变体重试。
    """
    if not path:
        return ""
    path = normalize_input(path)
    path = os.path.expanduser(path)
    if cwd is None:
        cwd = os.getcwd()
    if os.path.isabs(path):
        resolved = os.path.normpath(path)
    else:
        resolved = os.path.normpath(os.path.join(cwd, path))

    if os.path.exists(resolved):
        return resolved
    for variant in _macos_path_variants(resolved):
        if os.path.exists(variant):
            return variant
    return resolved
