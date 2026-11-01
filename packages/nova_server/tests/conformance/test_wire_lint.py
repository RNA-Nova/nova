"""生成物 lint（codex 生成物 lint 测试对位——线上约定机械执法）。

扫描入库的 ``nova-wire.schema.json`` 全树，把"活在评审者脑子里"的线上
约定变成机器断言：

1. **camelCase**：线上键一律 camelCase——任何 ``properties`` 键含 ``_``
   即违例（snake_case 泄漏）；
2. **事件必有判别字段**：``*Event`` 定义的 ``properties`` 必须含 ``type``；
3. **模型变体联合必有判别键**：成员全为 ``$ref`` 的 ``anyOf``/``oneOf``
   必须带 ``discriminator``——开放集联合（含 ``CustomItem``/
   ``CustomAgentMessage``/``NovaItem`` 兜底成员）豁免（``docs/crosscutting/data-modeling.md``
   规则 6：兜底成员吸收未知变体，判别键管不了开放尾巴）。
"""

import json
from pathlib import Path

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "nova_client"
    / "protocol"
    / "nova-wire.schema.json"
)

# 开放集兜底成员（含它们的联合是开放集，不发射判别键——见 data-modeling.md）
_OPEN_SET_MEMBERS = {"CustomItem", "CustomAgentMessage", "NovaItem"}


def _load() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _walk(node, path=""):
    """遍历 schema 全树，产出 (path, node)。"""
    if isinstance(node, dict):
        yield path, node
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")


def test_wire_keys_are_camel_case():
    violations = []
    for path, node in _walk(_load()):
        props = node.get("properties")
        if isinstance(props, dict):
            for key in props:
                if "_" in key:
                    violations.append(f"{path}.{key}")
    assert not violations, "线上键必须是 camelCase：\n" + "\n".join(violations)


def test_events_have_type_discriminator_field():
    violations = []
    for name, node in _load().get("$defs", {}).items():
        if not name.endswith("Event"):
            continue
        props = node.get("properties")
        if isinstance(props, dict) and "type" not in props:
            violations.append(name)
    assert not violations, "事件定义缺 type 判别字段：" + ", ".join(violations)


def test_model_unions_have_discriminator():
    violations = []
    for path, node in _walk(_load()):
        for key in ("anyOf", "oneOf"):
            union = node.get(key)
            if not (isinstance(union, list) and len(union) > 1):
                continue
            if not all(isinstance(m, dict) and "$ref" in m for m in union):
                continue
            refs = {m["$ref"].split("/")[-1] for m in union}
            if refs & _OPEN_SET_MEMBERS:
                continue  # 开放集联合豁免
            if "discriminator" not in node:
                violations.append(path)
    assert not violations, "模型变体联合缺 discriminator：\n" + "\n".join(violations)
