# definition/loader.py

"""
AgentDefinitor - 文件加载模块

处理所有文件系统IO操作，简化逻辑（单层路径，无回退）。
"""

import glob
import json
import os
import re
from typing import List, Optional

from .types import Section, ToolInfo


def load_text_file(file_path: str) -> Optional[str]:
    """安全加载文本文件."""
    if not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return content if content else None
    except (IOError, UnicodeDecodeError):
        return None


def load_json_file(file_path: str) -> Optional[dict]:
    """安全加载JSON文件."""
    if not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def load_tools(tools_file: str) -> List[ToolInfo]:
    """
    加载 tools.json 为 ToolInfo 列表.
    
    格式示例：
    [
      {"name": "read_file", "description": "读取文件..."},
      {"name": "write_file", "description": "写入文件..."}
    ]
    """
    data = load_json_file(tools_file)
    if not isinstance(data, list):
        return []

    tools = []
    for item in data:
        if isinstance(item, dict) and "name" in item:
            tools.append(ToolInfo(
                name=item["name"],
                description=item.get("description", "")
            ))
    return tools


def load_sections(sections_dir: str, source_label: str = "system") -> List[Section]:
    """
    加载指定目录的 Markdown 文件为 Section 列表.
    
    按数字前缀排序（01-, 02-...），无数字前缀的按文件名字母排序。
    
    Args:
        sections_dir: 目录路径
        source_label: 来源标识（system/user/setup 等）
    """
    if not os.path.exists(sections_dir) or not os.path.isdir(sections_dir):
        return []

    md_files = glob.glob(os.path.join(sections_dir, "*.md"))
    if not md_files:
        return []

    # 排序：先按数字前缀，再按文件名
    def sort_key(path: str) -> tuple:
        filename = os.path.basename(path)
        match = re.match(r"^(\d+)[-_]", filename)
        if match:
            return (0, int(match.group(1)), filename)
        return (1, 0, filename)

    md_files.sort(key=sort_key)
    
    sections = []
    for order, filepath in enumerate(md_files, start=1):
        content = load_text_file(filepath)
        if content is None:
            continue
            
        filename = os.path.basename(filepath)
        # 清理名称：移除数字前缀和扩展名
        clean_name = re.sub(r"^\d+[-_]", "", filename)
        clean_name = clean_name.replace(".md", "")
        clean_name = clean_name.replace("-", " ").replace("_", " ")
        
        sections.append(Section(
            name=clean_name,
            order=order,
            content=content,
            source=f"{source_label}:{filename}"
        ))

    return sections


def load_user_sections_recursive(user_dir: str) -> List[Section]:
    """
    递归加载 user/ 目录的所有 Markdown 文件.
    
    保持目录结构，路径作为 section name 的一部分。
    """
    sections = []
    
    if not os.path.exists(user_dir):
        return sections

    # 收集所有 md 文件
    md_files = []
    for root, _, files in os.walk(user_dir):
        for filename in sorted(files):
            if filename.endswith(".md"):
                filepath = os.path.join(root, filename)
                relpath = os.path.relpath(filepath, user_dir)
                md_files.append((filepath, relpath))

    # 按相对路径排序确保稳定顺序
    md_files.sort(key=lambda x: x[1])

    for order, (filepath, relpath) in enumerate(md_files, start=1):
        content = load_text_file(filepath)
        if content:
            # name 使用相对路径（去掉 .md）
            name = relpath.replace(".md", "").replace(os.sep, "/")
            sections.append(Section(
                name=name,
                order=order,
                content=content,
                source=f"user:{relpath}"
            ))

    return sections