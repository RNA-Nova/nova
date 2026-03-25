import os
from typing import Optional

def get_folder_tree(path: str, max_depth: Optional[int] = None, prefix: str = "", is_last: bool = True, current_depth: int = 0, show_root_path: bool = True) -> str:
    """
    生成树形目录结构文本
    
    Args:
        path: 文件夹路径
        max_depth: 最大深度
        prefix: 前缀字符
        is_last: 是否是最后一个项目
        current_depth: 当前深度
        show_root_path: 是否显示根目录的完整路径
    
    Returns:
        树形格式的目录文本
    """
    if max_depth is not None and current_depth > max_depth:
        return ""
    
    if not os.path.exists(path):
        return f"[错误] 路径不存在: {path}\n"
    
    # 当前目录的名称
    result = ""
    if current_depth == 0:
        # 根目录显示完整路径
        if show_root_path:
            result = f"{os.path.abspath(path)}/\n"
        else:
            result = f"{os.path.basename(path) or path}/\n"
    else:
        result = f"{prefix}{'└── ' if is_last else '├── '}{os.path.basename(path)}/\n"
    
    try:
        items = sorted(os.listdir(path))
    except PermissionError:
        return result + f"{prefix}    [权限不足]\n"
    
    # 处理子项目
    new_prefix = prefix + ("    " if is_last else "│   ")
    
    for i, item in enumerate(items):
        item_path = os.path.join(path, item)
        is_last_item = (i == len(items) - 1)
        
        if os.path.isdir(item_path):
            result += get_folder_tree(item_path, max_depth, new_prefix, is_last_item, current_depth + 1, show_root_path)
        else:
            # 文件信息
            try:
                size = os.path.getsize(item_path)
                size_str = format_size(size)
                result += f"{new_prefix}{'└── ' if is_last_item else '├── '}{item} ({size_str})\n"
            except (OSError, PermissionError):
                result += f"{new_prefix}{'└── ' if is_last_item else '├── '}{item} [无法读取大小]\n"
    
    return result

def format_size(size: int) -> str:
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"