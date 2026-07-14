"""文件 IO 工具函数。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


def load_text_file(file_path: str) -> Optional[str]:
    """安全加载文本文件，不存在或为空时返回 None。"""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content if content else None
    except (IOError, UnicodeDecodeError):
        return None


def load_json_file(file_path: str) -> Optional[Dict[str, Any]]:
    """安全加载 JSON 文件，不存在或解析失败时返回 None。"""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def save_json_file(file_path: str, data: Dict[str, Any]) -> None:
    """安全保存 JSON 文件，自动创建父目录。"""
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def canonicalize_path(path: str) -> str:
    """返回文件的真实绝对路径，解析所有 symlink。

    用于检测通过不同路径引用的同一文件（如 editable 安装的 symlink）。
    """
    try:
        return os.path.realpath(path)
    except OSError:
        return os.path.abspath(path)


__all__ = [
    "canonicalize_path",
    "load_json_file",
    "save_json_file",
    "load_text_file",
]
