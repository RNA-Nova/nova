"""线上契约导出（构建期）——事件/会话条目类型 → JSON Schema + TypeScript。

线上形状即 ``serialize.py`` 的直通语义：pydantic 模型按 ``model_dump(mode="json")``
落线、dataclass 按 ``vars`` 递归落线（``str`` 子类 Enum 取 value，其余 Enum
取 ``str(member)``，不可建模值降级为字符串/``unknown``）。本模块的类型 walker
复刻同一语义，保证导出契约与线上实际形状一致，而非"理论上应该的形状"。

导出集（与 RPC 桥覆盖面一一对应）：
- ``AgentSessionEvent``（Bus 2 全集）→ ``NovaEventEnvelope``（``{type, data}`` 信封）；
- ``SessionEntry``（会话条目全集，``getSessionEntries`` 数据源）→ ``NovaSessionEntry``；
- ``SessionHeader``（会话文件头）→ 命名模型，不入条目联合。

用法（仓库根目录执行）：

    python -m nova_harness.core.rpc.protocol.schema_export \
        --schema packages/nova-tui/protocol/nova-wire.schema.json \
        --ts packages/nova-tui/src/protocol/nova-wire.gen.ts

两个工件均**入仓**：schema 漂移经 git diff 审查；``tests`` 中的漂移测试
保证类型变更后必须重新导出。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import types as _py_types
from dataclasses import is_dataclass
from datetime import date, datetime, time
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from nova_harness.core.types.events.unions import AgentSessionEvent
from nova_harness.core.types.session.entries import SessionEntry, SessionHeader

# 契约版本（major/minor 语义）：
# - MAJOR：事件词汇/方法形状发生**不兼容**变更（删字段/改语义）时递增——
#   前端 major 不等即硬拒（握手响亮失败）；
# - MINOR：**加法**变更（新事件/新方法/新可选字段）时递增——minor 差放行，
#   前端靠能力位与未知事件静默忽略降级。
# schema 工件与 ``initialize`` 握手均携带两者，TS 侧常量三处同源。
CONTRACT_VERSION_MAJOR = 1
CONTRACT_VERSION_MINOR = 3

# ---------------------------------------------------------------------------
# 类型 walker：annotation → （TS 类型字符串, JSON Schema 片段）
# ---------------------------------------------------------------------------


class _Registry:
    """命名类型注册表：嵌套 dataclass/pydantic 模型只生成一次。"""

    def __init__(self) -> None:
        self.names: Dict[Any, str] = {}
        self.ts_defs: Dict[str, str] = {}
        self.schema_defs: Dict[str, Dict[str, Any]] = {}
        # 递归保护：先登记名字再生成字段
        self._in_progress: set[Any] = set()


def _unwrap(annotation: Any) -> Any:
    """剥掉 Annotated / SerializeAsAny 等包装，取真实注解。"""
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    origin_name = getattr(origin, "__name__", "")
    if str(origin).startswith("typing.Annotated") or origin_name == "Annotated":
        return _unwrap(get_args(annotation)[0])
    if origin_name == "SerializeAsAny":
        return _unwrap(get_args(annotation)[0])
    return annotation


def _is_union(annotation: Any) -> bool:
    origin = get_origin(annotation)
    return origin is Union or (origin is not None and origin is _py_types.UnionType)


def _enum_ts_and_schema(enum_cls: type) -> Tuple[str, Dict[str, Any]]:
    """Enum 的线上形态：str 子类取 value；其余取 str(member)（serialize 降级语义）。"""
    if issubclass(enum_cls, str):
        values = [member.value for member in enum_cls]
    else:
        values = [str(member) for member in enum_cls]
    ts = " | ".join(json.dumps(v) for v in values)
    return ts, {"type": "string", "enum": values}


def _ts_name(py_type: Any) -> str:
    return getattr(py_type, "__name__", str(py_type))


def _is_pydantic_model(py_type: Any) -> bool:
    try:
        from pydantic import BaseModel
    except ImportError:  # pragma: no cover - pydantic 必然存在
        return False
    return isinstance(py_type, type) and issubclass(py_type, BaseModel)


def _fields_of(py_type: Any) -> List[Tuple[str, Any]]:
    """返回 ``[(线上字段名, 解析后注解)]``，同时支持 dataclass 与 pydantic 模型。

    pydantic 模型取 **alias**（NovaBaseModel 的 to_camel 生成——线上 camelCase
    契约与导出同源）；dataclass 取字段名原样。
    """
    if _is_pydantic_model(py_type):
        return [
            (field.alias or name, _unwrap(field.annotation))
            for name, field in py_type.model_fields.items()
        ]
    hints = get_type_hints(py_type)
    return [
        (f.name, _unwrap(hints.get(f.name, f.type)))
        for f in dataclasses.fields(py_type)
    ]


def _has_default(py_type: Any, field_name: str) -> bool:
    """字段是否带默认值——params 模型可选性（``?``）的依据。

    与后端校验同源：pydantic 的 ``is_required()`` / dataclass 的
    ``default|default_factory``。仅用于 params 模型（结果模型线上恒含全部
    字段，不走这里）。
    ``field_name`` 是线上名（alias——to_camel）；pydantic 按 alias 反查字段。
    """
    if _is_pydantic_model(py_type):
        for name, field in py_type.model_fields.items():
            if (field.alias or name) == field_name:
                return not field.is_required()
        return False
    if is_dataclass(py_type):
        for f in dataclasses.fields(py_type):
            if f.name == field_name:
                return (
                    f.default is not dataclasses.MISSING
                    or f.default_factory is not dataclasses.MISSING  # type: ignore[comparison-overlap]
                )
    return False


def _literal_default(py_type: Any, field_name: str) -> Optional[Any]:
    """取判别字段的 Literal 值（``type: Literal["x"] = "x"``）。"""
    for name, ann in _fields_of(py_type):
        if name != field_name:
            continue
        if get_origin(ann) is Literal:
            args = get_args(ann)
            if args:
                return args[0]
    return None


class _Walker:
    """单一 annotation walker，同时产出 TS 与 JSON Schema。"""

    def __init__(self) -> None:
        self.reg = _Registry()
        from nova_agent import AgentMessage
        from nova_ai import Message

        # 具名 Union 别名：字段引用处发射别名而非内联（TS 消费方需要
        # ``AgentMessage`` 这样的具名类型做参数标注）
        self._aliases: List[Tuple[str, Any]] = [
            ("AgentMessage", AgentMessage),
            ("Message", Message),
        ]
        self.ts_type_aliases: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # annotation → TS / schema
    # ------------------------------------------------------------------

    def map(self, annotation: Any) -> Tuple[str, Dict[str, Any]]:
        annotation = _unwrap(annotation)

        if annotation is Any or annotation is object or annotation is None:
            return "unknown", {}

        # 具名 Union 别名：发射别名引用并确保成员已注册
        for alias_name, alias_ann in self._aliases:
            if annotation is alias_ann:
                if alias_name not in self.ts_type_aliases:
                    member_ts: List[str] = []
                    member_refs: List[Dict[str, Any]] = []
                    for member in get_args(alias_ann):
                        ts, schema = self.map(member)
                        member_ts.append(ts)
                        member_refs.append(schema)
                    self.ts_type_aliases[alias_name] = (
                        f"export type {alias_name} = {' | '.join(member_ts)};"
                    )
                    self.reg.schema_defs[alias_name] = {"anyOf": member_refs}
                return alias_name, {"$ref": f"#/$defs/{alias_name}"}

        if annotation in (str, datetime, date, time, bytes):
            return "string", {"type": "string"}
        if annotation in (int, float):
            return "number", {"type": "number"}
        if annotation is bool:
            return "boolean", {"type": "boolean"}
        if annotation is type(None):
            return "null", {"type": "null"}

        origin = get_origin(annotation)

        if origin is Literal:
            values = list(get_args(annotation))
            ts = " | ".join(json.dumps(v) for v in values)
            schema: Dict[str, Any] = {"enum": values}
            if all(isinstance(v, str) for v in values):
                schema["type"] = "string"
            return ts, schema

        if _is_union(annotation):
            args = [a for a in get_args(annotation)]
            ts_parts: List[str] = []
            schema_parts: List[Dict[str, Any]] = []
            for arg in args:
                ts, schema = self.map(arg)
                ts_parts.append(ts)
                schema_parts.append(schema)
            return " | ".join(ts_parts), {"anyOf": schema_parts}

        if origin in (list, set, frozenset) or (
            origin is not None
            and getattr(origin, "__name__", "") in ("Sequence", "Iterable")
        ):
            args = get_args(annotation)
            item_ann = args[0] if args else Any
            ts, schema = self.map(item_ann)
            return f"({ts})[]" if " | " in ts else f"{ts}[]", {
                "type": "array",
                "items": schema,
            }

        if origin is tuple:
            return "unknown[]", {"type": "array"}

        if origin is not None and getattr(origin, "__name__", "") in (
            "dict",
            "Dict",
            "Mapping",
        ):
            args = get_args(annotation)
            value_ann = args[1] if len(args) == 2 else Any
            ts, schema = self.map(value_ann)
            if ts == "unknown":
                return "Record<string, unknown>", {"type": "object"}
            return f"Record<string, {ts}>", {
                "type": "object",
                "additionalProperties": schema,
            }

        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return _enum_ts_and_schema(annotation)

        if is_dataclass(annotation) or _is_pydantic_model(annotation):
            return self._named_model(annotation)

        # 其余（Callable / AbortSignal / 任意实例类型）：线上降级为字符串或
        # 不透明值（serialize 的 str() 兜底），契约上记为 unknown
        return "unknown", {}

    def map_params(self, py_type: Any) -> Tuple[str, Dict[str, Any]]:
        """params 模型专用映射：带默认值的字段在 TS 标记可选（``?``）。

        与后端 pydantic 校验的默认值语义对齐——调用方省略可缺省字段。
        仅顶层字段生效；嵌套模型保持全量（传入即完整对象）。
        """
        py_type = _unwrap(py_type)
        if is_dataclass(py_type) or _is_pydantic_model(py_type):
            return self._named_model(py_type, optional_defaults=True)
        return self.map(py_type)

    # ------------------------------------------------------------------
    # 命名模型（dataclass / pydantic）
    # ------------------------------------------------------------------

    def _named_model(
        self, py_type: Any, *, optional_defaults: bool = False
    ) -> Tuple[str, Dict[str, Any]]:
        name = _ts_name(py_type)
        if name in self.reg.ts_defs:
            return name, {"$ref": f"#/$defs/{name}"}
        if py_type in self.reg._in_progress:
            return name, {"$ref": f"#/$defs/{name}"}

        # RootModel（裸列表结果）：不解构为接口，直接映射 root 注解
        from pydantic import RootModel

        if isinstance(py_type, type) and issubclass(py_type, RootModel):
            root_ann = _unwrap(py_type.model_fields["root"].annotation)
            ts, schema = self.map(root_ann)
            self.reg.ts_defs[name] = f"export type {name} = {ts};"
            self.reg.schema_defs[name] = schema
            return name, {"$ref": f"#/$defs/{name}"}

        self.reg._in_progress.add(py_type)
        try:
            fields = _fields_of(py_type)
            if not fields:
                # 空模型（如 CustomAgentMessage 基类）：线上是不透明对象
                self.reg.ts_defs[name] = (
                    f"export interface {name} {{\n  [key: string]: unknown;\n}}"
                )
                self.reg.schema_defs[name] = {"type": "object"}
                return name, {"$ref": f"#/$defs/{name}"}

            ts_lines: List[str] = []
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for field_name, ann in fields:
                ts, schema = self.map(ann)
                # params 模型：带默认值的字段可选（调用方可省略，与后端
                # 校验的默认值语义一致）；结果模型不走此路（线上 dump 恒含
                # 全部字段）。注意按名缓存——同名模型先入为主，params/结果
                # 命名分离（*Params/*Result）是既有约定，不会相撞。
                optional = optional_defaults and _has_default(py_type, field_name)
                ts_lines.append(f"  {field_name}{'?' if optional else ''}: {ts};")
                properties[field_name] = schema
                if not optional:
                    required.append(field_name)
            body = "\n".join(ts_lines)
            self.reg.ts_defs[name] = f"export interface {name} {{\n{body}\n}}"
            self.reg.schema_defs[name] = {
                "type": "object",
                "title": name,
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            return name, {"$ref": f"#/$defs/{name}"}
        finally:
            self.reg._in_progress.discard(py_type)

    # ------------------------------------------------------------------
    # 根类型
    # ------------------------------------------------------------------

    def add_union_root(self, root: Any) -> List[Any]:
        """展开 Union 根，注册全部成员，返回成员类型列表。"""
        members = list(get_args(root)) if _is_union(root) else [root]
        for member in members:
            member = _unwrap(member)
            if is_dataclass(member) or _is_pydantic_model(member):
                self._named_model(member)
        return members


def _collect_method_shapes() -> Dict[str, Any]:
    """装配完整方法注册表并读取形状（方法表 = 注册处声明，零漂移）。"""
    from nova_harness.core.rpc.protocol.methods import (
        register_auth_methods,
        register_model_methods,
        register_package_methods,
        register_resources_methods,
        register_session_methods,
        register_settings_methods,
        register_system_methods,
        register_user_tools_methods,
    )
    from nova_harness.core.rpc.protocol.methods.state import ServerState
    from nova_harness.core.rpc.protocol.router import MethodRegistry

    registry = MethodRegistry()
    state = ServerState()
    for register in (
        register_session_methods,
        register_model_methods,
        register_auth_methods,
        register_resources_methods,
        register_settings_methods,
        register_user_tools_methods,
        register_system_methods,
        register_package_methods,
    ):
        register(registry, state)
    return registry.shapes()


# ---------------------------------------------------------------------------
# 工件生成
# ---------------------------------------------------------------------------

_HEADER = """/**
 * GENERATED — 请勿手改。
 *
 * 由 nova_harness 的线上契约导出生成：
 *   python -m nova_harness.core.rpc.protocol.schema_export
 * 类型真理在 Python 运行时（事件即线上事实），本文件是其构建期快照。
 */
"""


def _default_repo_root() -> Path:
    # .../packages/nova_harness/src/nova_harness/core/rpc/protocol/schema_export.py
    # parents[6] = <repo>/packages
    return Path(__file__).resolve().parents[6].parent


def build_artifacts() -> Tuple[Dict[str, Any], str]:
    """生成 JSON Schema dict 与 TS 源码字符串。"""
    walker = _Walker()

    event_members = walker.add_union_root(AgentSessionEvent)
    entry_members = walker.add_union_root(SessionEntry)
    walker._named_model(SessionHeader)

    # ---- TS：信封联合 ----
    envelope_variants: List[str] = []
    schema_envelope_variants: List[Dict[str, Any]] = []
    for member in event_members:
        member = _unwrap(member)
        if not (is_dataclass(member) or _is_pydantic_model(member)):
            continue
        type_value = _literal_default(member, "type")
        if not isinstance(type_value, str):
            continue
        name = _ts_name(member)
        # 信封锚点（连接化 P2）：seq/ts/sessionId 由服务器在广播时打戳——
        # syncSession 高水位对账与多端扇出的依据
        envelope_variants.append(
            f"  | {{ type: {json.dumps(type_value)}; data: {name}; "
            "seq: number; ts: number; sessionId: string | null }"
        )
        schema_envelope_variants.append(
            {
                "type": "object",
                "properties": {
                    "type": {"const": type_value},
                    "data": {"$ref": f"#/$defs/{name}"},
                    "seq": {"type": "integer"},
                    "ts": {"type": "integer"},
                    "sessionId": {"type": ["string", "null"]},
                },
                "required": ["type", "data", "seq", "ts", "sessionId"],
                "additionalProperties": False,
            }
        )

    entry_names = [
        _ts_name(_unwrap(m))
        for m in entry_members
        if is_dataclass(_unwrap(m)) or _is_pydantic_model(_unwrap(m))
    ]

    # ---- 方法形状（注册表即方法表，零漂移） ----
    method_shapes = _collect_method_shapes()
    methods_schema: Dict[str, Any] = {}
    ts_method_entries: List[str] = []
    for method_name, shape in sorted(method_shapes.items()):
        if shape.params_model is not None:
            params_ts, params_schema = walker.map_params(shape.params_model)
        else:
            params_ts, params_schema = "Record<string, unknown>", {"type": "object"}
        if shape.result_model is not None:
            result_ts, result_schema = walker.map(shape.result_model)
        else:
            result_ts, result_schema = "unknown", {}
        methods_schema[method_name] = {
            "domain": shape.domain,
            "params": params_schema,
            "result": result_schema,
        }
        ts_method_entries.append(
            f"  {json.dumps(method_name)}: {{ params: {params_ts}; result: {result_ts} }};"
        )

    ts_parts: List[str] = [_HEADER]
    ts_parts.append(f"export const NOVA_CONTRACT_MAJOR = {CONTRACT_VERSION_MAJOR};")
    ts_parts.append(f"export const NOVA_CONTRACT_MINOR = {CONTRACT_VERSION_MINOR};\n")
    ts_parts.append("// ---- 模型定义 ----\n")
    for name in sorted(walker.reg.ts_defs):
        ts_parts.append(walker.reg.ts_defs[name] + "\n")
    if walker.ts_type_aliases:
        ts_parts.append("// ---- Union 别名 ----\n")
        for name in walker.ts_type_aliases:
            ts_parts.append(walker.ts_type_aliases[name] + "\n")
    ts_parts.append("// ---- 信封与根联合 ----\n")
    ts_parts.append(
        "export type NovaEventEnvelope =\n" + "\n".join(envelope_variants) + ";\n"
    )
    entry_union = " | ".join(entry_names)
    ts_parts.append(f"export type NovaSessionEntry = {entry_union};\n")
    ts_parts.append("// ---- 方法形状 ----\n")
    ts_parts.append(
        "export interface NovaWireMethodMap {\n"
        + "\n".join(ts_method_entries)
        + "\n}\n"
    )
    ts_parts.append("export type NovaWireMethod = keyof NovaWireMethodMap;\n")

    # ---- JSON Schema ----
    defs = dict(walker.reg.schema_defs)
    schema: Dict[str, Any] = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "NovaWireProtocol",
        "contractVersionMajor": CONTRACT_VERSION_MAJOR,
        "contractVersionMinor": CONTRACT_VERSION_MINOR,
        "novaEvent": {"oneOf": schema_envelope_variants},
        "sessionEntry": {
            "oneOf": [{"$ref": f"#/$defs/{name}"} for name in entry_names]
        },
        "methods": methods_schema,
        "$defs": defs,
    }
    return schema, "\n".join(ts_parts).rstrip() + "\n"


def main() -> int:
    # Windows 控制台缺省 cp1252 编码，中文输出会炸 UnicodeEncodeError——
    # 显式重配 UTF-8（POSIX 下是无害 no-op）
    import sys as _sys

    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="导出线上契约（JSON Schema + TS）")
    root = _default_repo_root()
    parser.add_argument(
        "--schema",
        default=str(root / "packages/nova-tui/protocol/nova-wire.schema.json"),
    )
    parser.add_argument(
        "--ts",
        default=str(root / "packages/nova-tui/src/protocol/nova-wire.gen.ts"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验入仓工件是否最新，不写入（漂移测试用）",
    )
    args = parser.parse_args()

    schema, ts_source = build_artifacts()
    schema_text = (
        json.dumps(schema, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )

    if args.check:
        stale = []
        for path, content in ((args.schema, schema_text), (args.ts, ts_source)):
            existing = (
                Path(path).read_text(encoding="utf-8") if Path(path).exists() else None
            )
            if existing != content:
                stale.append(path)
        if stale:
            print(
                "线上契约工件已漂移，请重新导出：\n  "
                + "\n  ".join(stale)
                + "\n命令：python -m nova_harness.core.rpc.protocol.schema_export"
            )
            return 1
        print("线上契约工件为最新。")
        return 0

    for path, content in ((args.schema, schema_text), (args.ts, ts_source)):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        print(f"written: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
