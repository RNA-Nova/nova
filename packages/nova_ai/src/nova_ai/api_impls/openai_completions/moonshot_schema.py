"""Moonshot-flavored JSON Schema 子集规范化（nova 特有，pi 无对应件）。

Kimi/Moonshot 系端点（kimi-coding / moonshotai / api.kimi.com /
api.moonshot.*）对工具 ``parameters`` 执行收紧的 schema 校验。经真实
端点探针实锤的规则：

1. **根部必须 ``type: "object"``**（缺即 400 "type is required and must
   be \\"object\\""）；
2. **``anyOf`` 子项必须各自声明 ``type``**——父层的 ``type`` 不顶子项的
   （缺即 400 "when using anyOf, type should be defined in anyOf items
   instead of the parent schema"，生产环境真实命中）；
3. 父层 ``type`` 与子项各自 ``type`` 共存**合法**（探针 200）。

pydantic 风格的 ``Optional[X]`` 等产物天然不犯此规则，但第三方包作者的
手写 schema 无法保证——在引擎层做一次规范化，把整轮 400 变成可服务请求。
规范化只做一件事：**父层 ``type`` 补进缺型的 ``anyOf`` 子项（父层
``type`` 保留——规则 1 要求根部有型）**。语义保持：原交集语义 ≡ 子项
各自补型；子项已声明 ``type`` 的保持不动（恒真/矛盾是作者原义，引擎
不改判）；无法推断的（父层也无型）留原样——那是作者 bug，端点报错
文案本身已足够指路。
"""

import copy
from typing import Any, Dict


def _supplement_any_of_item_types(node: Dict[str, Any]) -> None:
    """单节点规范化：``type`` + ``anyOf`` 共存时给缺型子项补父层型（就地）。

    父层 ``type`` **保留**（根部 ``type: "object"`` 是必选项，探针实锤）。
    """
    if "type" not in node or "anyOf" not in node:
        return
    parent_type = node["type"]
    items = node["anyOf"]
    if not isinstance(items, list):
        return
    for i, item in enumerate(items):
        if isinstance(item, dict) and "type" not in item:
            items[i] = {**item, "type": parent_type}


def _walk(node: Any) -> None:
    if isinstance(node, dict):
        _supplement_any_of_item_types(node)
        for value in node.values():
            _walk(value)
    elif isinstance(node, list):
        for item in node:
            _walk(item)


def normalize_moonshot_flavored_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """返回规范化后的新 schema（不改动入参——工具定义是跨请求共享状态）。"""
    normalized = copy.deepcopy(schema)
    _walk(normalized)
    return normalized


__all__ = ["normalize_moonshot_flavored_schema"]
