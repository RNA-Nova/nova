"""模型数据管线的共享库（对齐 TS ``scripts/model-data.ts``）。

生成器（``generate.py``）与一致性检查（``check_model_data.py``）
共用这里的数据结构定义、manifest 读写与四方对账校验：

    聚合器（models_generated.py 的 PROVIDER_IDS）
      ↔ 生成 shard（providers/<module>/models.py）
      ↔ 数据分片（providers/data/<provider_id>.json）
      ↔ manifest（providers/data/.manifest.json 的 schemaVersion/哈希）

任何一处漂移，``validate_generated_model_data`` 抛错——构建/测试即红。
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, TypeGuard

from nova_protocol import ClassifierModel, ImageModel, Model, NovaBaseModel
from pydantic.alias_generators import to_camel

MODEL_DATA_SCHEMA_VERSION = 1
MODEL_DATA_MANIFEST_FILE = ".manifest.json"

_AGGREGATOR_REL = Path("src") / "nova_ai" / "models_generated.py"
_DATA_DIR_REL = Path("src") / "nova_ai" / "providers" / "data"


def default_package_root() -> Path:
    """monorepo 源码树中的 nova_ai 包根；仅生成/校验工具使用。"""
    return Path(__file__).resolve().parents[3] / "nova_ai"


def generated_aggregator_path(package_root: Path) -> Path:
    return package_root / _AGGREGATOR_REL


def generated_providers_dir(package_root: Path) -> Path:
    return package_root / _DATA_DIR_REL.parent


def generated_data_dir(package_root: Path) -> Path:
    return package_root / _DATA_DIR_REL


def generated_shard_path(package_root: Path, provider_id: str) -> Path:
    return (
        generated_providers_dir(package_root)
        / provider_id.replace("-", "_")
        / "models.py"
    )


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sorted_record(entries) -> Dict[str, Any]:
    return dict(sorted(entries))


def same_strings(a: Iterable[str], b: Iterable[str]) -> bool:
    la, lb = list(a), list(b)
    return len(la) == len(lb) and all(x == y for x, y in zip(la, lb))


def describe_set_difference(expected: Iterable[str], actual: Iterable[str]) -> str:
    expected_set, actual_set = set(expected), set(actual)
    missing = [v for v in expected if v not in actual_set]
    extra = [v for v in actual if v not in expected_set]
    parts = [
        f"missing: {', '.join(missing)}" if missing else "",
        f"extra: {', '.join(extra)}" if extra else "",
    ]
    return "; ".join(p for p in parts if p)


def assert_exact_model_ids(
    label: str, expected: Iterable[str], actual: Iterable[str]
) -> None:
    """两侧模型 id 集合必须完全一致（对齐 TS assertExactModelIds）。"""
    expected_ids = sorted(set(expected))
    actual_ids = sorted(set(actual))
    if same_strings(expected_ids, actual_ids):
        return
    raise ValueError(
        f"{label} model IDs do not match ({describe_set_difference(expected_ids, actual_ids)})"
    )


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def read_json_object(
    path: Path, description: str, errors: List[str]
) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(
            path.read_text("utf-8"), parse_constant=_reject_non_finite_json
        )
    except Exception as error:
        errors.append(f"{description} is not valid JSON: {error}")
        return None
    if not isinstance(parsed, dict):
        errors.append(f"{description} must contain a JSON object")
        return None
    return parsed


# ---------------------------------------------------------------------------
# 聚合器 / 结构读取
# ---------------------------------------------------------------------------


def _parse_python(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text("utf-8"), filename=str(path))
    except SyntaxError as error:
        raise ValueError(f"{path} contains invalid Python: {error}") from error


def _top_level_assignments(tree: ast.Module) -> Dict[str, ast.AST]:
    assignments: Dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value: Optional[ast.AST] = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assignments[target.id] = value
    return assignments


def _has_binding(
    tree: ast.Module, from_module: Optional[str], name: str, before_line: int
) -> bool:
    """运行时绑定必须在使用前以原名导入，且导入后不得被覆盖。"""
    found = False
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if (alias.asname or alias.name) != name:
                    continue
                matches = (
                    isinstance(node, ast.Import)
                    if from_module is None
                    else isinstance(node, ast.ImportFrom)
                    and node.module == from_module
                    and node.level == 0
                ) and alias.name == name
                if found:
                    raise ValueError(f"Generated shard import {name} is rebound")
                if matches and node.lineno < before_line:
                    found = True
            continue
        if not found:
            continue
        rebinds = (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ) or any(
            isinstance(child, ast.Name)
            and isinstance(child.ctx, ast.Store)
            and child.id == name
            for child in ast.walk(node)
        )
        if rebinds:
            raise ValueError(f"Generated shard import {name} is rebound")
    return found


def _validate_shard_data(
    tree: ast.Module, assignments: Dict[str, ast.AST], provider_id: str
) -> None:
    """每个 shard 只加载一次自己的 JSON，两个目录共享 ``_DATA``。"""
    value = assignments.get("_DATA")
    expected = ast.parse(
        f'json.loads((files("nova_ai.providers") / "data" / "{provider_id}.json").read_text("utf-8"))',
        mode="eval",
    ).body
    if value is None or ast.dump(value, include_attributes=False) != ast.dump(
        expected, include_attributes=False
    ):
        raise ValueError("Generated shard _DATA must load its own model data once")
    declarations = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_DATA"
            for target in node.targets
        )
    ]
    if len(declarations) != 1:
        raise ValueError("Generated shard _DATA must have exactly one assignment")
    required = ((None, "json"), ("importlib.resources", "files"))
    if not all(
        _has_binding(tree, module, name, declarations[0].lineno)
        for module, name in required
    ):
        raise ValueError("Generated shard _DATA has missing, aliased, or late imports")


def _validate_shard_catalog_value(
    tree: ast.Module, value: ast.AST, provider_id: str, variable: str
) -> None:
    """shard 目录必须是对共享 ``_DATA`` 的一次类型化拍平。"""
    if variable.endswith("_IMAGE_MODELS"):
        flatten_name = "flatten_image_model_catalog"
        model_name = "ImageModel"
    elif variable.endswith("_CLASSIFIER_MODELS"):
        flatten_name = "flatten_classifier_model_catalog"
        model_name = "ClassifierModel"
    else:
        flatten_name = "flatten_chat_model_catalog"
        model_name = "Model"
    if (
        not isinstance(value, ast.Call)
        or not isinstance(value.func, ast.Name)
        or value.func.id != flatten_name
    ):
        raise ValueError(f"Generated shard {variable} must call {flatten_name}")
    if len(value.args) != 2 or value.keywords:
        raise ValueError(f"Generated shard {variable} has invalid flatten arguments")
    provider_arg, data_arg = value.args
    if not isinstance(provider_arg, ast.Constant) or provider_arg.value != provider_id:
        raise ValueError(f"Generated shard {variable} has the wrong provider id")
    if not isinstance(data_arg, ast.Name) or data_arg.id != "_DATA":
        raise ValueError(f"Generated shard {variable} must consume shared _DATA")
    declarations = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Name) and target.id == variable
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
        )
    ]
    if len(declarations) != 1 or not isinstance(declarations[0], ast.AnnAssign):
        raise ValueError(f"Generated shard {variable} must have one precise annotation")
    expected_annotation = ast.parse(f"Dict[str, {model_name}]", mode="eval").body
    if ast.dump(declarations[0].annotation, include_attributes=False) != ast.dump(
        expected_annotation, include_attributes=False
    ):
        raise ValueError(
            f"Generated shard {variable} annotation must be Dict[str, {model_name}]"
        )
    required = (
        ("nova_ai.model_catalog", flatten_name),
        ("typing", "Dict"),
        ("nova_protocol", model_name),
    )
    if not all(
        _has_binding(tree, module, name, value.lineno) for module, name in required
    ):
        raise ValueError(
            f"Generated shard {variable} has missing, aliased, or late required imports"
        )


def _read_aggregator(
    package_root: Path,
) -> Tuple[List[str], Dict[str, str], Dict[str, str], Dict[str, str]]:
    """静态读取 id、provider 映射，并核对每个生成 shard 的 import。"""
    aggregator = generated_aggregator_path(package_root)
    tree = _parse_python(aggregator)
    assignments = _top_level_assignments(tree)
    ids_node = assignments.get("PROVIDER_IDS")
    if ids_node is None:
        raise ValueError(f"No PROVIDER_IDS literal found in {aggregator}")
    try:
        ids = ast.literal_eval(ids_node)
    except (SyntaxError, TypeError, ValueError):
        ids = None
    if (
        not isinstance(ids, list)
        or not ids
        or any(not isinstance(id, str) or not id for id in ids)
    ):
        raise ValueError(
            f"Generated aggregator contains no valid provider ids: {aggregator}"
        )
    if len(set(ids)) != len(ids):
        raise ValueError(
            f"Generated aggregator contains duplicate provider ids: {aggregator}"
        )

    def _mapping(name: str) -> Dict[str, str]:
        mapping_node = assignments.get(name)
        if not isinstance(mapping_node, ast.Dict):
            raise ValueError(
                f"Generated aggregator has no valid provider mapping: {name}"
            )
        mapping: Dict[str, str] = {}
        for key_node, value_node in zip(
            mapping_node.keys, mapping_node.values, strict=True
        ):
            if (
                not isinstance(key_node, ast.Constant)
                or not isinstance(key_node.value, str)
                or not key_node.value
                or not isinstance(value_node, ast.Name)
            ):
                raise ValueError(
                    f"Generated aggregator has an invalid provider mapping: {name}"
                )
            provider_id = key_node.value
            if provider_id in mapping:
                raise ValueError(
                    f"Generated aggregator has duplicate provider mapping: {provider_id}"
                )
            mapping[provider_id] = value_node.id
        return mapping

    chat_mapping = _mapping("GENERATED_PROVIDER_MODELS")
    image_mapping = _mapping("GENERATED_PROVIDER_IMAGE_MODELS")
    classifier_mapping = _mapping("GENERATED_PROVIDER_CLASSIFIER_MODELS")
    provider_ids = sorted(ids)
    expected_imports: Dict[str, Tuple[str, str]] = {}
    for provider_id in provider_ids:
        prefix = provider_id.upper().replace("-", "_")
        module = f"nova_ai.providers.{provider_id.replace('-', '_')}.models"
        for suffix in ("", "_IMAGE", "_CLASSIFIER"):
            variable = f"{prefix}{suffix}_MODELS"
            expected_imports[variable] = (module, variable)

    actual_imports: Dict[str, Tuple[str, str]] = {}
    actual_import_lines: Dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level != 0:
            raise ValueError("Generated aggregator imports must be absolute")
        for alias in node.names:
            name = alias.asname or alias.name
            if name in actual_imports:
                raise ValueError(f"Generated aggregator has duplicate import: {name}")
            actual_imports[name] = (node.module or "", alias.name)
            actual_import_lines[name] = node.lineno
    if actual_imports != expected_imports:
        mismatches = [
            f"{name}: expected {expected}, got {actual_imports.get(name)}"
            for name, expected in expected_imports.items()
            if actual_imports.get(name) != expected
        ]
        extra = sorted(set(actual_imports) - set(expected_imports))
        raise ValueError(
            "Generated aggregator imports do not match provider shards: "
            + "; ".join([*mismatches, *(f"extra: {name}" for name in extra)])
        )
    reference_lines: Dict[str, int] = {}
    for mapping_name in (
        "GENERATED_PROVIDER_MODELS",
        "GENERATED_PROVIDER_IMAGE_MODELS",
        "GENERATED_PROVIDER_CLASSIFIER_MODELS",
    ):
        for node in ast.walk(assignments[mapping_name]):
            if isinstance(node, ast.Name) and node.id in expected_imports:
                reference_lines[node.id] = min(
                    node.lineno, reference_lines.get(node.id, node.lineno)
                )
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for name, import_line in actual_import_lines.items():
            reference_line = reference_lines.get(name)
            if reference_line is None or not import_line < node.lineno < reference_line:
                continue
            rebinds = (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == name
            ) or any(
                isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Store)
                and child.id == name
                for child in ast.walk(node)
            )
            if rebinds:
                raise ValueError(
                    f"Generated aggregator import {name} is rebound before its reference"
                )
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            name = alias.asname or alias.name
            reference_line = reference_lines.get(name)
            if reference_line is not None and node.lineno > reference_line:
                raise ValueError(
                    f"Generated aggregator import {name} occurs after its reference"
                )
    return provider_ids, chat_mapping, image_mapping, classifier_mapping


def read_provider_ids(package_root: Path) -> List[str]:
    """从生成的聚合器读取 provider id 清单（对齐 TS readModelDataProviderIds）。"""
    return _read_aggregator(package_root)[0]


def read_provider_structure(data_dir: Path, provider_id: str) -> Dict[str, str]:
    """读单个数据分片 → ``{模型 id: api}``（组间重复 id 视为错误）。"""
    errors: List[str] = []
    path = data_dir / f"{provider_id}.json"
    groups = read_json_object(path, f"{provider_id}.json", errors)
    if groups is None:
        raise ValueError("\n".join(errors))
    models: Dict[str, str] = {}
    for api, value in groups.items():
        if not isinstance(value, dict):
            raise ValueError(f"{path} API group {api!r} must be an object")
        for model_id in value:
            if model_id in models:
                raise ValueError(
                    f"{path} contains model {model_id} in more than one API group"
                )
            models[model_id] = api
    if not models:
        raise ValueError(f"{path} contains no generated model data")
    return dict(sorted(models.items()))


def read_model_data_structure(package_root: Path) -> Dict[str, Dict[str, str]]:
    """对账聚合器、shard 与数据分片，并返回 typed-key/API 结构。"""
    provider_ids, chat_mapping, image_mapping, classifier_mapping = _read_aggregator(
        package_root
    )
    for mapping in (chat_mapping, image_mapping, classifier_mapping):
        if not same_strings(provider_ids, sorted(mapping)):
            raise ValueError(
                "Generated aggregator provider mapping does not match PROVIDER_IDS "
                f"({describe_set_difference(provider_ids, sorted(mapping))})"
            )

    providers_dir = generated_providers_dir(package_root)
    expected_shards = sorted(
        f"{provider_id.replace('-', '_')}/models.py" for provider_id in provider_ids
    )
    actual_shards = sorted(
        path.relative_to(providers_dir).as_posix()
        for path in providers_dir.glob("*/models.py")
    )
    if not same_strings(expected_shards, actual_shards):
        raise ValueError(
            "Generated model aggregator and provider shards do not match "
            f"({describe_set_difference(expected_shards, actual_shards)})"
        )
    for provider_id in provider_ids:
        prefix = provider_id.upper().replace("-", "_")
        shard = generated_shard_path(package_root, provider_id)
        shard_tree = _parse_python(shard)
        assignments = _top_level_assignments(shard_tree)
        for mapping, suffix in (
            (chat_mapping, ""),
            (image_mapping, "_IMAGE"),
            (classifier_mapping, "_CLASSIFIER"),
        ):
            variable = f"{prefix}{suffix}_MODELS"
            if mapping[provider_id] != variable:
                raise ValueError(
                    "Generated aggregator provider mapping has a wrong variable for "
                    f"{provider_id}: {mapping[provider_id]}"
                )
            if variable not in assignments:
                raise ValueError(f"Generated shard {shard} is missing {variable}")
            _validate_shard_catalog_value(
                shard_tree, assignments[variable], provider_id, variable
            )
        _validate_shard_data(shard_tree, assignments, provider_id)

    data_dir = generated_data_dir(package_root)
    return {pid: read_provider_structure(data_dir, pid) for pid in provider_ids}


def model_data_structure_hash(structure: Dict[str, Dict[str, str]]) -> str:
    normalized = sorted_record(
        (pid, sorted_record(models.items())) for pid, models in structure.items()
    )
    return sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False))


def create_model_data_manifest(
    structure: Dict[str, Dict[str, str]],
    file_contents: Dict[str, str],
    generated_at: str,
) -> Dict[str, Any]:
    return {
        "schemaVersion": MODEL_DATA_SCHEMA_VERSION,
        "generatedAt": generated_at,
        "structureHash": model_data_structure_hash(structure),
        "files": dict(
            sorted((name, sha256(content)) for name, content in file_contents.items())
        ),
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _throw_validation_errors(errors: List[str]) -> None:
    visible = errors[:30]
    suffix = (
        f"\n  ... and {len(errors) - len(visible)} more"
        if len(errors) > len(visible)
        else ""
    )
    raise ValueError(
        "Invalid generated model data:\n"
        + "\n".join(f"  - {e}" for e in visible)
        + suffix
    )


def _is_finite_number(value: Any) -> TypeGuard[float | int]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _validate_cost_value(value: Any, label: str, errors: List[str]) -> None:
    """校验原始 JSON 费率；主模型与 fallback 条目共用同一不变量。"""
    if not isinstance(value, dict):
        errors.append(f"{label} has invalid cost metadata")
        return
    for field in ("input", "output", "cache_read", "cache_write"):
        # ModelCost 的默认值负责运行期省略字段，不代表生成目录可以缺字段。
        if not _is_finite_number(value.get(field)):
            errors.append(f"{label} has invalid cost.{field}")
    tiers = value.get("tiers")
    if tiers is None:
        return
    if not isinstance(tiers, list):
        errors.append(f"{label} has invalid cost.tiers")
        return
    for index, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            errors.append(f"{label} has invalid cost.tiers[{index}]")
            continue
        for field in ("input", "output", "cache_read", "cache_write"):
            if not _is_finite_number(tier.get(field)):
                errors.append(f"{label} has invalid cost.tiers[{index}].{field}")
        thresholds = [
            key for key in ("inputTokensAbove", "input_tokens_above") if key in tier
        ]
        if not thresholds:
            errors.append(f"{label} has invalid cost.tiers[{index}].inputTokensAbove")
        for key in thresholds:
            if not _is_finite_number(tier[key]):
                errors.append(f"{label} has invalid cost.tiers[{index}].{key}")


def _model_field_inputs(model: NovaBaseModel) -> Dict[str, str]:
    """生成物输入键 → 模型字段名（snake/camel/显式 alias）。"""
    allowed: Dict[str, str] = {}
    for name, field in type(model).model_fields.items():
        allowed[name] = name
        allowed[to_camel(name)] = name
        if isinstance(field.alias, str):
            allowed[field.alias] = name
    return allowed


def _normalize_validated_enums(raw: Any, validated: Any) -> Any:
    """仅把合法 JSON 枚举值替换成既有 Enum；其他输入原样留给 strict 校验。"""
    if isinstance(validated, Enum):
        return validated if raw == validated.value else raw
    if isinstance(validated, NovaBaseModel) and isinstance(raw, dict):
        normalized = dict(raw)
        for key, name in _model_field_inputs(validated).items():
            if key in raw:
                normalized[key] = _normalize_validated_enums(
                    raw[key], getattr(validated, name)
                )
        return normalized
    if isinstance(validated, (list, tuple)) and isinstance(raw, (list, tuple)):
        return [
            _normalize_validated_enums(raw_item, validated_item)
            for raw_item, validated_item in zip(raw, validated)
        ]
    return raw


def _validate_no_dropped_fields(
    raw: Any, validated: Any, label: str, errors: List[str]
) -> None:
    """封闭生成物边界：递归严格验类型，并拒绝被静默丢弃的未知键。"""
    if isinstance(validated, NovaBaseModel) and isinstance(raw, dict):
        # before-validator 可能重新构造嵌套模型，故每层用实际判别后的类型
        # 直接 strict 校验；合法枚举字符串先归一为既有 Enum。
        normalized = _normalize_validated_enums(raw, validated)
        try:
            type(validated).model_validate(normalized, strict=True)
        except ValueError as error:
            errors.append(f"{label} has invalid strictly typed fields: {error}")
        allowed = _model_field_inputs(validated)
        for key in sorted(set(raw) - set(allowed)):
            errors.append(f"{label} has unknown field {key!r}")
        for key, name in allowed.items():
            if key in raw:
                _validate_no_dropped_fields(
                    raw[key], getattr(validated, name), f"{label}.{key}", errors
                )
    elif isinstance(validated, (list, tuple)) and isinstance(raw, (list, tuple)):
        for index, (raw_item, validated_item) in enumerate(zip(raw, validated)):
            _validate_no_dropped_fields(
                raw_item, validated_item, f"{label}[{index}]", errors
            )


def _validate_model_value(
    value: Any,
    provider_id: str,
    model_key: str,
    expected_api: str,
    errors: List[str],
) -> None:
    """按 typed key 验收原始不变量及对应操作模型的形状。"""
    model_type, separator, model_id = model_key.partition(":")
    label = f"{provider_id}/{model_key}"
    if (
        not separator
        or model_type not in ("chat", "image", "classifier")
        or not model_id
    ):
        errors.append(f"{label} has an invalid typed model key")
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    if value.get("id") != model_id:
        errors.append(f"{label} has id {value.get('id')!r}, expected {model_id!r}")
    if value.get("provider") != provider_id:
        errors.append(
            f"{label} has provider {value.get('provider')!r}, expected {provider_id!r}"
        )
    if value.get("api") != expected_api:
        errors.append(
            f"{label} has api {value.get('api')!r}, expected {expected_api!r}"
        )
    actual_type = value.get("type") or "chat"
    if actual_type != model_type:
        errors.append(f"{label} has type {actual_type!r}, expected {model_type!r}")

    # 这些检查针对 Pydantic 默认值/coercion，避免把缺费率和字符串标量
    # 静默解释成合法成品；其余字段与 compat 形状由具体模型类验收。
    if not isinstance(value.get("name"), str) or not value.get("name"):
        errors.append(f"{label} has no model name")
    if not isinstance(value.get("base_url"), str):
        errors.append(f"{label} has no base_url string")
    input_types = value.get("input_types")
    if (
        not isinstance(input_types, list)
        or not input_types
        or any(entry not in ("text", "image") for entry in input_types)
    ):
        errors.append(f"{label} has invalid input modalities")
    _validate_cost_value(value.get("cost"), label, errors)

    compat = value.get("compat")
    fallbacks = (
        compat.get("allowed_fallback_models") if isinstance(compat, dict) else None
    )
    if isinstance(fallbacks, list):
        for index, entry in enumerate(fallbacks):
            if isinstance(entry, dict):
                _validate_cost_value(
                    entry.get("cost"),
                    f"{label}.compat.allowed_fallback_models[{index}]",
                    errors,
                )
    try:
        validated: NovaBaseModel
        if model_type == "image":
            output_types = value.get("output_types")
            if (
                not isinstance(output_types, list)
                or "image" not in output_types
                or any(entry not in ("text", "image") for entry in output_types)
            ):
                errors.append(f"{label} has invalid output modalities")
            for field in ("reasoning", "context_window", "max_tokens"):
                if field in value:
                    errors.append(f"{label} has unsupported chat field {field}")
            validated = ImageModel.model_validate(value, strict=True)
        elif model_type == "classifier":
            for field in ("reasoning", "max_tokens", "output_types"):
                if field in value:
                    errors.append(f"{label} has unsupported chat field {field}")
            context_window = value.get("context_window")
            if not _is_finite_number(context_window) or context_window <= 0:
                errors.append(f"{label} has invalid context_window")
            validated = ClassifierModel.model_validate(value, strict=True)
        else:
            if "output_types" in value:
                errors.append(f"{label} has unsupported image field output_types")
            if not isinstance(value.get("reasoning"), bool):
                errors.append(f"{label} has no reasoning boolean")
            for field in ("context_window", "max_tokens"):
                number = value.get(field)
                if not _is_finite_number(number) or number <= 0:
                    errors.append(f"{label} has invalid {field}")
            validated = Model.model_validate(value, strict=True)
        _validate_no_dropped_fields(value, validated, label, errors)
    except ValueError as error:
        errors.append(f"{label} does not match the Model schema: {error}")


def validate_model_data_directory(
    structure: Dict[str, Dict[str, str]], data_dir: Path
) -> None:
    """数据目录四方对账（对齐 TS validateModelDataDirectory）。"""
    if not data_dir.is_dir():
        raise ValueError(f"Generated model data directory does not exist: {data_dir}")

    errors: List[str] = []
    expected_files = sorted(f"{pid}.json" for pid in structure)
    actual_files = sorted(
        entry.name
        for entry in data_dir.iterdir()
        if entry.name.endswith(".json") and entry.name != MODEL_DATA_MANIFEST_FILE
    )
    if not same_strings(expected_files, actual_files):
        errors.append(
            "provider data files do not match the generated catalog "
            f"({describe_set_difference(expected_files, actual_files)})"
        )

    manifest_path = data_dir / MODEL_DATA_MANIFEST_FILE
    manifest = read_json_object(manifest_path, "model data manifest", errors)
    manifest_files: Optional[Dict[str, Any]] = None
    if manifest is not None:
        schema_version = manifest.get("schemaVersion")
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != MODEL_DATA_SCHEMA_VERSION
        ):
            errors.append(
                f"model data schema is {schema_version!r}, "
                f"expected {MODEL_DATA_SCHEMA_VERSION}"
            )
        generated_at = manifest.get("generatedAt")
        if not isinstance(generated_at, str):
            errors.append("model data manifest has an invalid generation timestamp")
        else:
            try:
                parsed = datetime.fromisoformat(generated_at)
                if parsed.tzinfo is None:
                    raise ValueError("timestamp must include a timezone")
            except ValueError:
                errors.append("model data manifest has an invalid generation timestamp")
        if manifest.get("structureHash") != model_data_structure_hash(structure):
            errors.append(
                "model data generation stamp does not match the generated catalog"
            )
        raw_files = manifest.get("files")
        if not isinstance(raw_files, dict):
            errors.append("model data manifest has no file hashes")
        else:
            manifest_files = raw_files
            if not same_strings(expected_files, sorted(raw_files)):
                errors.append(
                    "manifest file hashes do not match provider data files "
                    f"({describe_set_difference(expected_files, sorted(raw_files))})"
                )

    for provider_id, expected_models in structure.items():
        filename = f"{provider_id}.json"
        path = data_dir / filename
        if not path.exists():
            continue
        content = path.read_text("utf-8")
        if manifest_files is not None and manifest_files.get(filename) != sha256(
            content
        ):
            errors.append(f"{filename} does not match its manifest hash")
        groups = read_json_object(path, filename, errors)
        if groups is None:
            continue

        actual_models: Dict[str, str] = {}
        for api, value in groups.items():
            if not isinstance(value, dict):
                errors.append(f"{filename} API group {api!r} must be an object")
                continue
            for model_id, model in value.items():
                if model_id in actual_models:
                    errors.append(
                        f"{provider_id}/{model_id} appears in more than one API group"
                    )
                    continue
                actual_models[model_id] = api
                _validate_model_value(model, provider_id, model_id, api, errors)

        assert_exact_model_ids(
            f"{filename}", expected_models.keys(), actual_models.keys()
        )
        for model_id, expected_api in expected_models.items():
            actual_api = actual_models.get(model_id)
            if actual_api is not None and actual_api != expected_api:
                errors.append(
                    f"{provider_id}/{model_id} is grouped under API {actual_api!r}, "
                    f"expected {expected_api!r}"
                )

    if errors:
        _throw_validation_errors(errors)


def validate_generated_model_data(package_root: Path) -> None:
    """聚合器、shard、数据分片与 manifest 的完整对账。"""
    structure = read_model_data_structure(package_root)
    validate_model_data_directory(structure, generated_data_dir(package_root))
