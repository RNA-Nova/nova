"""
JSON解析工具
用于解析流式响应中的部分JSON
"""

import json
from typing import Any, Dict, List, Optional, Union

from json_repair import repair_json


def parse_streaming_json(json_str: Optional[str]) -> Union[Dict[str, Any], List[Any]]:
    """
    解析流式响应中的部分JSON

    始终返回一个有效的对象，即使JSON不完整。

    Args:
        json_str: 流式响应中的部分JSON字符串

    Returns:
        解析后的对象，如果解析失败则返回空对象
    """
    if not json_str or json_str.strip() == "":
        return {}

    # 首先尝试标准解析（对于完整JSON最快）
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        pass

    # 使用 json_repair 修复并解析
    try:
        repaired = repair_json(json_str)
        if repaired:
            return json.loads(repaired)
    except Exception:
        pass

    # 如果所有解析都失败，返回空对象
    return {}
