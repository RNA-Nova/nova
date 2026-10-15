"""executor 配置根 loader——executor 栈客户端半边的发现层。

定位（定案）：执行策略词汇（沙箱套餐/网络代理/审批档）归 executor 栈自持，
不进 agent core——harness settings 不携带任何执行词汇。本模块是 PROTOCOL
v1.4 `environmentConfig/read` 层栈的客户端半边，同两层、同格式：

- user 层：`<exec-server home>/config.toml`（TOML；home = `NOVA_EXEC_SERVER_HOME`
  覆盖，缺省 `~/.nova/exec-server`）
- project 层：`<cwd>/.nova/settings.json` 的 `exec-server` 段（JSON；
  **仅当 `project_trusted=True` 时读取**——Project Trust 裁决归 harness，
  本层只消费布尔结论，不做信任判断）

合并纪律（对位 codex `config/src/merge.rs`）：表（dict）按键深合并；
**列表与标量整体覆盖**（高层替换低层——codex 语义：legacy arrays replace
wholesale，不做列表追加）。未知键 warn-and-ignore（前向兼容——新版写旧版
读不炸）；文件缺失 = 空层；解析失败 = ConfigError（配置是用户资产，坏文件
要响亮地报，不静默吞）。

远程说明：本 loader 读**客户端机器**的盘——策略在客户端物化、展开后经
process/start 下发，executor 不合并不裁决。需要执行机本机配置的场景走
`environmentConfig/read` 代读端点（本模块不管）。
"""

from __future__ import annotations

import json
import logging
import os
import tomllib
from pathlib import Path
from typing import Any

from nova_protocol import (
    LOCAL_ENVIRONMENT_ID,
    MAX_ENVIRONMENT_ID_LENGTH,
    ApprovalPolicy,
    ExecutorConfig,
    ExecutorEnvironment,
    NetworkMode,
    NetworkProxySettings,
    SandboxMode,
    SandboxWorkspaceWriteConfig,
)
from pydantic import BaseModel, ValidationError

from .errors import ConfigError

logger = logging.getLogger(__name__)

#: executor 家目录环境变量覆盖（对位 rust executor 的同名旋钮）
NOVA_EXEC_SERVER_HOME_ENV = "NOVA_EXEC_SERVER_HOME"

#: user 层配置文件名（位于 executor home 下；对位 executor-protocol v1.4 层栈）
USER_CONFIG_FILE_NAME = "config.toml"

#: project 层位置（与 nova 体系项目级配置一致；executor 词汇住其中的
#: exec-server 段）
PROJECT_CONFIG_DIR_NAME = ".nova"
PROJECT_CONFIG_FILE_NAME = "settings.json"
PROJECT_SECTION_KEY = "exec-server"

#: 端点 id 最大长度（对位 codex MAX_ENVIRONMENT_ID_LEN）——常量本体在
#: nova_protocol（上方导入），此处不再重定义（曾本地重赋同值，值漂移时会
#: 静默分叉；审计范围C C2）


def default_exec_server_home() -> Path:
    """exec-server 家目录：`NOVA_EXEC_SERVER_HOME` 覆盖，缺省 `~/.nova/exec-server`"""
    override = os.environ.get(NOVA_EXEC_SERVER_HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".nova" / "exec-server"


def load_executor_config(
    cwd: str | Path | None = None,
    *,
    project_trusted: bool = False,
    executor_home: str | Path | None = None,
) -> ExecutorConfig:
    """加载并合并 executor 配置层栈。

    - `cwd`：项目根（定位 project 层）；为 None 时只读 user 层
    - `project_trusted`：Project Trust 结论（harness 裁决后传入）——False 时
      project 层即使存在也不读（恶意仓库无法经配置弱化执行姿态）
    - `executor_home`：测试/特殊部署的显式覆盖（优先级高于环境变量）
    """
    home = (
        Path(executor_home) if executor_home is not None else default_exec_server_home()
    )
    layers: list[tuple[str, dict[str, Any], Path]] = []

    user_file = home / USER_CONFIG_FILE_NAME
    layers.append((f"user:{user_file}", _read_toml_layer(user_file), home))

    if cwd is not None and project_trusted:
        project_file = Path(cwd) / PROJECT_CONFIG_DIR_NAME / PROJECT_CONFIG_FILE_NAME
        layers.append(
            (
                f"project:{project_file}",
                _read_project_layer(project_file),
                project_file.parent,
            )
        )

    merged: dict[str, Any] = {}
    for _source, content, _layer_dir in layers:
        merged = _merge_layer(merged, content)
    # 列表整体覆盖语义下 merged 的 environments 来自最后一个携带该键的层；
    # 其配置文件目录即 stdio 相对 cwd 的解析基准（对位 codex config_dir）
    environments_dir: Path | None = None
    for _source, content, layer_dir in layers:
        if "environments" in content:
            environments_dir = layer_dir
    return _validate(
        merged,
        sources=[source for source, _, _ in layers],
        config_dir=environments_dir,
    )


# ---------------------------------------------------------------------------
# 内部实现
# ---------------------------------------------------------------------------


def _read_toml_layer(path: Path) -> dict[str, Any]:
    """user 层（TOML）：缺失 = 空层；解析失败 = ConfigError"""
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"executor user 层配置解析失败（{path}）：{error}") from error
    if not isinstance(data, dict):  # TOML 恒 dict，防御性断言
        raise ConfigError(f"executor user 层配置必须是表（{path}）")
    return data


def _read_project_layer(path: Path) -> dict[str, Any]:
    """project 层：`<cwd>/.nova/settings.json` 的 `exec-server` 段"""
    if not path.is_file():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(
            f"executor project 层配置解析失败（{path}）：{error}"
        ) from error
    if not isinstance(settings, dict):
        raise ConfigError(f"project settings 必须是对象（{path}）")
    section = settings.get(PROJECT_SECTION_KEY)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ConfigError(f"project 层 `{PROJECT_SECTION_KEY}` 段必须是对象（{path}）")
    return section


def _merge_layer(low: dict[str, Any], high: dict[str, Any]) -> dict[str, Any]:
    """单层合并（对位 codex merge.rs）：表按键深合并；列表/标量整体覆盖"""
    out = dict(low)
    for key, value in high.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _merge_layer(existing, value)
        else:
            out[key] = value
    return out


def _validate(
    merged: dict[str, Any],
    *,
    sources: list[str],
    config_dir: Path | None = None,
) -> ExecutorConfig:
    """合并产物 → 校验后的 ExecutorConfig。

    `config_dir`：environments 注册表来源配置文件的目录（对位 codex 同名
    参数）——stdio 条目的相对 cwd 按它解析；None 时相对 cwd 即 ConfigError。
    """
    _warn_unknown_keys(merged, ExecutorConfig, path=())
    try:
        config = ExecutorConfig.model_validate(merged)
    except ValidationError as error:
        raise ConfigError(
            f"executor 配置校验失败（层来源：{', '.join(sources)}）：{error}"
        ) from error
    return _validate_environments(config, config_dir=config_dir)


def _validate_environments(
    config: ExecutorConfig, *, config_dir: Path | None = None
) -> ExecutorConfig:
    """环境注册表校验（逐条对位 codex environment_toml.rs 的校验规则）+
    stdio 相对 cwd 按配置目录归一（codex normalize_stdio_cwd）。

    返回校验通过且 cwd 已归一的配置（无相对 cwd 时原样返回）。运行时动态
    注册（EnvironmentManager.upsert_environment）**不复用本校验**——字符集/
    字节长是配置文件命名仪式，运行时走 `validate_runtime_environment`。
    """
    seen: set[str] = set()
    resolved: list[ExecutorEnvironment] = []
    changed = False
    for environment in config.environments:
        eid = environment.id
        # id 校验顺序对位 codex validate_environment_id：
        # 空 → 首尾空白 → 保留字 → 字节长度 → 字符集
        trimmed = eid.strip()
        if not trimmed:
            raise ConfigError("环境 id 为空")
        if trimmed != eid:
            raise ConfigError(f"环境 id `{eid}` 含首尾空白")
        # 保留字（对位 codex）：内建 local 精确匹配；"none" ASCII 忽略大小写
        if eid == LOCAL_ENVIRONMENT_ID or eid.lower() == "none":
            raise ConfigError(f"环境 id `{eid}` 是保留字")
        if len(eid.encode("utf-8")) > MAX_ENVIRONMENT_ID_LENGTH:
            raise ConfigError(
                f"环境 id `{eid}` 超长（>{MAX_ENVIRONMENT_ID_LENGTH} 字节）"
            )
        if not all(c.isascii() and (c.isalnum() or c in "-_") for c in eid):
            raise ConfigError(f"环境 id `{eid}` 只能含 ASCII 字母数字、'-' 或 '_'")
        if eid in seen:
            raise ConfigError(f"环境 id 重复：`{eid}`")
        seen.add(eid)
        _validate_environment_fields(environment)
        normalized = _normalize_stdio_cwd(environment, config_dir)
        if normalized is not environment:
            changed = True
        resolved.append(normalized)
    default = config.default_environment
    if default is not None:
        stripped = default.strip()
        if not stripped:
            raise ConfigError("default_environment 不能为空串")
        # 候选集对位 codex：注册表 id ∪（include_local 时预置的内建
        # local——`default_environment = "local"` 合法）
        candidates = seen | ({LOCAL_ENVIRONMENT_ID} if config.include_local else set())
        if stripped.lower() != "none" and stripped not in candidates:
            raise ConfigError(
                f"default_environment `{stripped}` 未在 environments 中注册"
            )
    if not changed:
        return config
    return config.model_copy(update={"environments": resolved})


def _validate_environment_fields(environment: ExecutorEnvironment) -> None:
    """单条结构不变量（装载/运行时共用）：url/program 二选一 + 交叉字段 +
    url scheme + program 非空白"""
    eid = environment.id
    if (environment.url is None) == (environment.program is None):
        raise ConfigError(f"环境 `{eid}` 必须且只能设置 url 或 program 之一")
    # 交叉校验（对位 codex parse_environment_toml）：args/env/cwd 依附
    # program，connect_timeout 依附 url。注：codex 按键存在性判定
    # （Option），本侧模型 args/env 缺省即空容器、按非空判定——
    # `args = []` 配 url 的显式空集边缘 codex 拒、本侧收（模型形状
    # 分辨不出缺席与显式空集）
    if environment.program is None and (
        environment.args or environment.env or environment.cwd is not None
    ):
        raise ConfigError(f"环境 `{eid}` 的 args/env/cwd 需要设置 program")
    if environment.url is None and environment.connect_timeout_sec is not None:
        raise ConfigError(f"环境 `{eid}` 的 connect_timeout_sec 需要设置 url")
    if environment.url is not None:
        url = environment.url.strip()
        if not url.startswith(("ws://", "wss://")):
            raise ConfigError(f"环境 `{eid}` 的 url 必须是 ws:// 或 wss://")
    if environment.program is not None and not environment.program.strip():
        raise ConfigError(f"环境 `{eid}` 的 program 不能为空")


def validate_runtime_environment(
    environment: ExecutorEnvironment,
) -> ExecutorEnvironment:
    """运行时动态注册的单条校验（EnvironmentManager.upsert_environment 复用）。

    比装载期 `_validate_environments` 少两道**配置文件命名仪式**（字符集/
    字节长度）——codex 的 validate_environment_id 只作用于配置文件条目；
    运行时注册（如 executor manager 以 canonical_url 作环境键）不应用配置
    命名纪律。仍强制：空/首尾空白/保留字（防内建 local/none 语义撞车）+
    结构不变量（防 get_client assert 崩）+ stdio 相对 cwd 拒绝（无来源
    目录，对位 codex "cwd must be absolute"）。
    """
    eid = environment.id
    trimmed = eid.strip()
    if not trimmed:
        raise ConfigError("环境 id 为空")
    if trimmed != eid:
        raise ConfigError(f"环境 id `{eid}` 含首尾空白")
    if eid == LOCAL_ENVIRONMENT_ID or eid.lower() == "none":
        raise ConfigError(f"环境 id `{eid}` 是保留字")
    _validate_environment_fields(environment)
    return _normalize_stdio_cwd(environment, None)


def _normalize_stdio_cwd(
    environment: ExecutorEnvironment, config_dir: Path | None
) -> ExecutorEnvironment:
    """stdio 相对 cwd 按配置目录拼绝对（对位 codex normalize_stdio_cwd）：
    无 cwd / 绝对 cwd 原样；相对且无来源目录 → ConfigError"""
    cwd = environment.cwd
    if cwd is None or environment.program is None:
        return environment
    if Path(cwd).is_absolute():
        return environment
    if config_dir is None:
        raise ConfigError(
            f"环境 `{environment.id}` 的 cwd 必须是绝对路径"
            "（相对 cwd 按配置文件目录解析，当前无来源目录）"
        )
    return environment.model_copy(update={"cwd": str(config_dir / cwd)})


def _warn_unknown_keys(
    data: dict[str, Any], model: type[BaseModel], *, path: tuple[str, ...]
) -> None:
    """未知键 warn-and-ignore（逐段递归，对位 codex 的前向兼容纪律）"""
    for key, value in data.items():
        field = model.model_fields.get(key)
        dotted = ".".join((*path, key))
        if field is None:
            logger.warning("executor 配置含未知键（忽略）：%s", dotted)
            continue
        # 已知键且目标字段是 BaseModel 子类 → 递归检查（dict 直递归；
        # list[Model]（如 environments）对元素逐项递归——未知键不静默吞）
        annotation = field.annotation
        target = _model_class(annotation)
        if isinstance(value, dict) and target is not None:
            _warn_unknown_keys(value, target, path=(*path, key))
        elif isinstance(value, list) and target is not None:
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _warn_unknown_keys(item, target, path=(*path, f"{key}[{index}]"))


def _model_class(annotation: Any) -> type[BaseModel] | None:
    """从字段注解里剥出 BaseModel 子类（穿 Optional/Union）"""
    import typing

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None
