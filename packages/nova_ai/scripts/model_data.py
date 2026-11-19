"""AI 本地模型目录的数据、聚合器/shard 结构与生成物对账。

本模块对位 pi ``packages/ai/scripts/model-data.ts``：AI 路径与 provider
读取、数据分片、manifest、严格字段校验和最终生成物检查都归消费方。
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TypeGuard

from nova_protocol import ClassifierModel, ImageModel, Model, ModelFields, NovaBaseModel
from pydantic.alias_generators import to_camel

MODEL_DATA_SCHEMA_VERSION = 1
MODEL_DATA_MANIFEST_FILE = ".manifest.json"


_AGGREGATOR_REL = Path("src") / "nova_ai" / "models_generated.py"
_DATA_DIR_REL = Path("src") / "nova_ai" / "providers" / "data"


def default_package_root() -> Path:
    """monorepo 源码树中的 nova_ai 包根；仅生成/校验工具使用。"""
    return Path(__file__).resolve().parent.parent


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
    """数据分片与 manifest 对账（对齐 TS validateModelDataDirectory）。"""
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


def read_provider_ids(package_root: Path) -> List[str]:
    """从聚合器的 ``PROVIDER_IDS`` 字面量读取 provider 清单。"""
    aggregator = generated_aggregator_path(package_root)
    tree = ast.parse(aggregator.read_text("utf-8"), filename=str(aggregator))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == "PROVIDER_IDS"
            for target in targets
        ):
            continue
        if node.value is None:
            raise ValueError(f"Invalid PROVIDER_IDS in {aggregator}")
        try:
            ids = ast.literal_eval(node.value)
        except (SyntaxError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid PROVIDER_IDS in {aggregator}") from error
        if (
            not isinstance(ids, list)
            or not ids
            or any(
                not isinstance(provider_id, str) or not provider_id
                for provider_id in ids
            )
        ):
            raise ValueError(f"Invalid PROVIDER_IDS in {aggregator}")
        if len(set(ids)) != len(ids):
            raise ValueError(f"Duplicate provider ids in {aggregator}")
        return sorted(ids)
    raise ValueError(f"No PROVIDER_IDS literal found in {aggregator}")


def read_model_data_structure(package_root: Path) -> Dict[str, Dict[str, str]]:
    """对账聚合器 provider、shard 文件集合与 JSON 数据结构。"""
    provider_ids = read_provider_ids(package_root)
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
    data_dir = generated_data_dir(package_root)
    return {
        provider_id: read_provider_structure(data_dir, provider_id)
        for provider_id in provider_ids
    }


def validate_generated_model_data(package_root: Path) -> None:
    """聚合器、shard、数据分片与 manifest 的完整对账。"""
    structure = read_model_data_structure(package_root)
    validate_model_data_directory(structure, generated_data_dir(package_root))


def _model_api(fields: ModelFields) -> str:
    api = fields.get("api")
    if not isinstance(api, str) or not api:
        raise ValueError("generated model is missing api")
    return api


def _model_key(model: ModelFields) -> str:
    model_id = model.get("id")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("generated model is missing id")
    model_type = model.get("type") or "chat"
    if model_type not in ("chat", "image", "classifier"):
        raise ValueError(f"generated model has unknown type: {model_type}")
    return f"{model_type}:{model_id}"


def group_provider_model_data(
    provider_id: str, models: List[ModelFields]
) -> tuple[Dict[str, Dict[str, ModelFields]], Dict[str, str]]:
    """按 API 分组并同时产出 manifest 结构（对位 pi groupProviderModelData）。"""
    groups: Dict[str, Dict[str, ModelFields]] = {}
    structure: Dict[str, str] = {}
    for fields in models:
        api = _model_api(fields)
        model_key = _model_key(fields)
        if model_key in structure:
            raise ValueError(f"{provider_id}/{model_key} has duplicate catalog entries")
        groups.setdefault(api, {})[model_key] = fields
        structure[model_key] = api
    ordered_groups = {
        api: {key: entries[key] for key in sorted(entries)}
        for api, entries in sorted(groups.items())
    }
    return ordered_groups, dict(sorted(structure.items()))
