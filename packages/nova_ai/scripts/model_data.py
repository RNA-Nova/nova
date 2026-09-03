"""模型数据管线的共享库（对齐 TS ``scripts/model-data.ts``）。

生成器（``generate_models.py``）与保鲜检查（``check_model_data.py``）
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
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

MODEL_DATA_SCHEMA_VERSION = 1
MODEL_DATA_MANIFEST_FILE = ".manifest.json"

_AGGREGATOR_REL = Path("src") / "nova_ai" / "models_generated.py"
_DATA_DIR_REL = Path("src") / "nova_ai" / "providers" / "data"
_PROVIDER_IDS_LITERAL = re.compile(
    r"PROVIDER_IDS(?::\s*list)?\s*=\s*(\[.*?\])", re.DOTALL
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


def read_json_object(
    path: Path, description: str, errors: List[str]
) -> Optional[Dict[str, Any]]:
    try:
        parsed = json.loads(path.read_text("utf-8"))
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


def read_provider_ids(package_root: Path) -> List[str]:
    """从生成的聚合器读取 provider id 清单（对齐 TS readModelDataProviderIds）。"""
    aggregator = package_root / _AGGREGATOR_REL
    text = aggregator.read_text("utf-8")
    match = _PROVIDER_IDS_LITERAL.search(text)
    if not match:
        raise ValueError(f"No PROVIDER_IDS literal found in {aggregator}")
    ids = ast.literal_eval(match.group(1))
    if not ids:
        raise ValueError(f"Generated aggregator contains no provider ids: {aggregator}")
    if len(set(ids)) != len(ids):
        raise ValueError(
            f"Generated aggregator contains duplicate provider ids: {aggregator}"
        )
    return list(ids)


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
    """全部 provider 的结构：``{provider_id: {模型 id: api}}``。"""
    provider_ids = read_provider_ids(package_root)
    data_dir = package_root / _DATA_DIR_REL
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


def _validate_model_value(
    value: Any,
    provider_id: str,
    model_id: str,
    expected_api: str,
    errors: List[str],
) -> None:
    label = f"{provider_id}/{model_id}"
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
    name = value.get("name")
    if not isinstance(name, str) or not name:
        errors.append(f"{label} has no model name")
    if not isinstance(value.get("base_url"), str):
        errors.append(f"{label} has no base_url string")
    if not isinstance(value.get("reasoning"), bool):
        errors.append(f"{label} has no reasoning boolean")
    input_types = value.get("input_types")
    if (
        not isinstance(input_types, list)
        or not input_types
        or any(entry not in ("text", "image") for entry in input_types)
    ):
        errors.append(f"{label} has invalid input modalities")
    context_window = value.get("context_window")
    if not isinstance(context_window, (int, float)) or context_window <= 0:
        errors.append(f"{label} has invalid context_window")
    max_tokens = value.get("max_tokens")
    if not isinstance(max_tokens, (int, float)) or max_tokens <= 0:
        errors.append(f"{label} has invalid max_tokens")
    cost = value.get("cost")
    if not isinstance(cost, dict):
        errors.append(f"{label} has invalid cost metadata")
    else:
        for field in ("input", "output", "cache_read", "cache_write"):
            entry = cost.get(field)
            if not isinstance(entry, (int, float)) or isinstance(entry, bool):
                errors.append(f"{label} has invalid cost.{field}")


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
        if manifest.get("schemaVersion") != MODEL_DATA_SCHEMA_VERSION:
            errors.append(
                f"model data schema is {manifest.get('schemaVersion')!r}, "
                f"expected {MODEL_DATA_SCHEMA_VERSION}"
            )
        generated_at = manifest.get("generatedAt")
        if not isinstance(generated_at, str):
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
    """入口校验：聚合器 ↔ shard ↔ 数据分片 ↔ manifest 四方对账。"""
    structure = read_model_data_structure(package_root)
    validate_model_data_directory(structure, package_root / _DATA_DIR_REL)
